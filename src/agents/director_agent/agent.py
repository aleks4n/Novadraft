"""Director agent — the pipeline's gap de-duplicator.

Sits between the paper agent and the validator. It drains the freshly mined gaps
from ``gaps/pending/`` and, for each one, decides whether it duplicates a gap the
workspace already knows about (anything in ``gaps/master/``, ``gaps/to_validate/``
or ``gaps/to_rank/``) or is novel:

- **duplicate** → merge its evidence into the matching known gap, then park the
  incoming copy in ``gaps/archive/`` marked ``status="duplicate"`` with
  ``duplicate_of`` pointing at the survivor. Nothing is deleted.
- **novel** → move it to ``gaps/to_validate/`` and expose its id in
  ``novel_gap_ids`` so the pipeline graph can fan the validator out over it.

Built as a LangGraph (``load_known → collect_pending → dedup → finalize``) so it
mounts as a subgraph node in ``src/graph/pipeline.py`` and streams/logs through the
shared ``GraphRunLog``. Duplicate detection uses a local-Qwen cosine-similarity
check, with a deterministic title/canonical fallback when torch/embeddings are
unavailable.
"""
from __future__ import annotations

import os
import sys
import json
from functools import lru_cache
from math import sqrt
from pathlib import Path
from typing import Any

try:
	import torch
	from transformers import AutoModel, AutoTokenizer
except ModuleNotFoundError:
	torch = None
	AutoModel = None
	AutoTokenizer = None

from langgraph.graph import END, START, StateGraph

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.common.utils import storage
from src.common.utils.logging.runlog import GraphRunLog
from src.common.utils.logging.trace import log_event
from src.agents.director_agent.state import DirectorState

# Stages that hold gaps the workspace already knows about — an incoming pending
# gap is a duplicate if it matches any of these. (pending/ is this agent's inbox;
# archive/ holds retired copies, not live gaps.)
_KNOWN_STAGES = ("master", "to_validate", "to_rank")


# --- duplicate detection (deterministic + optional local embeddings) ---------

def _gap_embedding_text(gap: dict[str, Any]) -> str:
	title = str(gap.get("title", "")).strip()
	description = str(
		gap.get("canonical_description") or gap.get("description") or ""
	).strip()
	parts: list[str] = []
	if title:
		parts.append(f"Title: {title}")
	if description:
		parts.append(f"Description: {description}")
	return "\n".join(parts).strip()


def _cosine_distance(left: list[float], right: list[float]) -> float:
	if len(left) != len(right) or not left:
		return 1.0

	dot_product = sum(a * b for a, b in zip(left, right))
	left_norm = sqrt(sum(value * value for value in left))
	right_norm = sqrt(sum(value * value for value in right))
	if not left_norm or not right_norm:
		return 1.0

	return 1.0 - (dot_product / (left_norm * right_norm))


class _LocalQwenEmbeddings:
	def __init__(self, model_name: str, max_length: int) -> None:
		if torch is None or AutoModel is None or AutoTokenizer is None:
			raise ModuleNotFoundError("Local embedding dependencies are unavailable")

		self.max_length = max_length
		self.device = "mps" if torch.backends.mps.is_available() else "cpu"
		dtype = torch.float16 if self.device == "mps" else torch.float32
		self.tokenizer = AutoTokenizer.from_pretrained(model_name, trust_remote_code=True)
		self.model = AutoModel.from_pretrained(
			model_name,
			trust_remote_code=True,
			torch_dtype=dtype,
		)
		self.model.to(self.device)
		self.model.eval()

	def _encode(self, texts: list[str]) -> list[list[float]]:
		inputs = self.tokenizer(
			texts,
			padding=True,
			truncation=True,
			max_length=self.max_length,
			return_tensors="pt",
		)
		inputs = {key: value.to(self.device) for key, value in inputs.items()}

		with torch.inference_mode():
			outputs = self.model(**inputs)

		hidden_states = outputs.last_hidden_state
		mask = inputs["attention_mask"].unsqueeze(-1).to(hidden_states.dtype)
		summed = (hidden_states * mask).sum(dim=1)
		counts = mask.sum(dim=1).clamp(min=1e-9)
		embeddings = summed / counts
		embeddings = torch.nn.functional.normalize(embeddings, p=2, dim=1)
		return [vector.detach().cpu().tolist() for vector in embeddings]

	def embed_documents(self, texts: list[str]) -> list[list[float]]:
		return self._encode(texts)


@lru_cache(maxsize=1)
def _safe_get_local_qwen_embeddings() -> Any | None:
    if torch is None or AutoModel is None or AutoTokenizer is None:
        return None

    model_name = os.getenv("NOVADRAFT_EMBEDDING_MODEL", "Qwen/Qwen3-Embedding-0.6B")
    max_length = int(os.getenv("NOVADRAFT_EMBEDDING_MAX_LENGTH", "512"))

    try:
        return _LocalQwenEmbeddings(model_name=model_name, max_length=max_length)
    except Exception:
        return None


def _gaps_are_similar(
	incoming_gap: dict[str, Any],
	existing_gap: dict[str, Any],
) -> bool:
	left_text = _gap_embedding_text(incoming_gap)
	right_text = _gap_embedding_text(existing_gap)
	if not left_text or not right_text:
		a_title = str(incoming_gap.get("title", "")).strip().lower()
		b_title = str(existing_gap.get("title", "")).strip().lower()
		a_canon = str(incoming_gap.get("canonical_description", "")).strip().lower()
		b_canon = str(existing_gap.get("canonical_description", "")).strip().lower()
		return (a_title and a_title == b_title) or (a_canon and a_canon == b_canon)

	embeddings = _safe_get_local_qwen_embeddings()
	if embeddings is None:
		a_title = str(incoming_gap.get("title", "")).strip().lower()
		b_title = str(existing_gap.get("title", "")).strip().lower()
		a_canon = str(incoming_gap.get("canonical_description", "")).strip().lower()
		b_canon = str(existing_gap.get("canonical_description", "")).strip().lower()
		return (a_title and a_title == b_title) or (a_canon and a_canon == b_canon)

	try:
		left_vector, right_vector = embeddings.embed_documents([left_text, right_text])
		distance = _cosine_distance(left_vector, right_vector)
		threshold = float(os.getenv("NOVADRAFT_GAP_COSINE_DISTANCE_THRESHOLD", "0.12"))
		return distance <= threshold
	except Exception:
		# Deterministic fallback if the embedding endpoint is unavailable or malformed.
		a_title = str(incoming_gap.get("title", "")).strip().lower()
		b_title = str(existing_gap.get("title", "")).strip().lower()
		a_canon = str(incoming_gap.get("canonical_description", "")).strip().lower()
		b_canon = str(existing_gap.get("canonical_description", "")).strip().lower()
		return (a_title and a_title == b_title) or (a_canon and a_canon == b_canon)


def _merge_evidence(existing_gap: dict[str, Any], incoming_gap: dict[str, Any]) -> None:
	existing_evidence = existing_gap.setdefault("evidence", [])
	if not isinstance(existing_evidence, list):
		existing_evidence = []
		existing_gap["evidence"] = existing_evidence

	incoming_evidence = incoming_gap.get("evidence", [])
	if not isinstance(incoming_evidence, list):
		return

	existing_serialized = {json.dumps(item, sort_keys=True) for item in existing_evidence}
	for evidence_item in incoming_evidence:
		serialized = json.dumps(evidence_item, sort_keys=True)
		if serialized not in existing_serialized:
			existing_evidence.append(evidence_item)
			existing_serialized.add(serialized)


# --- graph nodes -------------------------------------------------------------

def load_known(state: DirectorState) -> dict:
    """Load every gap the workspace already knows about (master + both inboxes),
    tagged with the stage folder it lives in so a merge can be written back."""
    slug = state["category_slug"]
    known: list[dict] = []
    for stage in _KNOWN_STAGES:
        for gap_id in storage.list_gap_ids(slug, stage):
            rec = storage.read_gap_json(slug, gap_id, stage)
            if rec is not None:
                known.append({"stage": stage, "record": rec})
    log_event(f"loaded {len(known)} known gap(s)", known=len(known))
    return {"known_gaps": known}


def _paper_gap_ids(slug: str, paper_id: str) -> list[str]:
    rec = storage.read_paper_json(slug, paper_id) or {}
    return list((rec.get("gaps") or {}).get("gap_ids") or [])


def _pending_scope(state: DirectorState) -> set[str] | None:
    """Which pending gap_ids this run may triage. ``None`` = no restriction (the
    whole inbox). Explicit ``scope_gap_ids`` wins; otherwise a ``paper_id`` scopes
    to that paper's gaps so ingest/resume is per-paper, not category-wide."""
    scope = state.get("scope_gap_ids")
    if scope:
        return set(scope)
    if state.get("paper_id"):
        return set(_paper_gap_ids(state["category_slug"], state["paper_id"]))
    return None


def collect_pending(state: DirectorState) -> dict:
    """Drain the freshly mined gaps waiting in gaps/pending/ — restricted to this
    run's scope (a paper's gaps / an explicit set) when one is given."""
    slug = state["category_slug"]
    scope = _pending_scope(state)
    pending: list[dict] = []
    for gap_id in storage.list_gap_ids(slug, "pending"):
        if scope is not None and gap_id not in scope:
            continue
        rec = storage.read_gap_json(slug, gap_id, "pending")
        if rec is not None:
            pending.append(rec)
    log_event(f"{len(pending)} pending gap(s) to triage", pending=len(pending))
    return {"pending_gaps": pending}


def dedup(state: DirectorState) -> dict:
    """Route each pending gap: merge duplicates into the matching known gap and
    archive the copy; move novel gaps to the validator inbox."""
    slug = state["category_slug"]
    known = list(state.get("known_gaps") or [])
    novel_ids: list[str] = []
    duplicate_ids: list[str] = []

    for gap in state.get("pending_gaps") or []:
        gid = gap.get("gap_id")
        if not gid:
            continue

        match = next((k for k in known if _gaps_are_similar(gap, k["record"])), None)

        if match is not None:
            survivor = match["record"]
            _merge_evidence(survivor, gap)
            storage.write_gap_json(slug, survivor, match["stage"])
            gap["status"] = "duplicate"
            gap["duplicate_of"] = survivor.get("gap_id")
            storage.write_gap_json(slug, gap, "archive")
            storage.remove_gap_json(slug, gid, "pending")
            duplicate_ids.append(gid)
            log_event(f"duplicate: {gid} -> {survivor.get('gap_id')}",
                      gap_id=gid, duplicate_of=survivor.get("gap_id"))
        else:
            storage.write_gap_json(slug, gap, "to_validate")
            storage.remove_gap_json(slug, gid, "pending")
            # A later pending gap can now dedupe against this one too.
            known.append({"stage": "to_validate", "record": gap})
            novel_ids.append(gid)
            log_event(f"novel: {gid} -> to_validate", gap_id=gid)

    return {"novel_gap_ids": novel_ids, "duplicate_gap_ids": duplicate_ids}


def finalize(state: DirectorState) -> dict:
    novel = state.get("novel_gap_ids") or []
    dupes = state.get("duplicate_gap_ids") or []
    return {"result": {
        "status": "ok",
        "category_slug": state["category_slug"],
        "n_pending": len(state.get("pending_gaps") or []),
        "novel_gap_ids": novel,
        "duplicate_gap_ids": dupes,
    }}


def build_director_graph():
    """Compile the director's dedup graph: load_known → collect_pending → dedup → finalize."""
    g = StateGraph(DirectorState)
    g.add_node("load_known", load_known)
    g.add_node("collect_pending", collect_pending)
    g.add_node("dedup", dedup)
    g.add_node("finalize", finalize)

    g.add_edge(START, "load_known")
    g.add_edge("load_known", "collect_pending")
    g.add_edge("collect_pending", "dedup")
    g.add_edge("dedup", "finalize")
    g.add_edge("finalize", END)
    return g.compile()


_GRAPH = None


def _get_graph():
    global _GRAPH
    if _GRAPH is None:
        _GRAPH = build_director_graph()
    return _GRAPH


def run(category_slug: str, *, stream: bool = True, echo: bool | None = None) -> dict:
    """De-duplicate the gaps waiting in ``gaps/pending/`` for ``category_slug``
    against the workspace's known gaps. Novel gaps move to ``gaps/to_validate/``;
    duplicates are merged into the matching gap and archived. Returns a summary
    dict (``novel_gap_ids`` / ``duplicate_gap_ids``). Standalone-callable.

    By default streams the graph through a :class:`GraphRunLog`, writing the
    standard per-run ``.jsonl`` to ``workspace/<slug>/logs/director_agent/`` and
    echoing to the terminal on a TTY. Pass ``stream=False`` for a quiet
    ``.invoke()``.
    """
    graph = _get_graph()
    inputs = {"category_slug": category_slug}
    if not stream:
        return graph.invoke(inputs)["result"]

    if echo is None:
        echo = sys.stderr.isatty()
    rl = GraphRunLog("director_agent", workspace=category_slug, run_hint=category_slug)
    rl.run(graph, inputs, echo=echo)
    if rl.error:
        raise RuntimeError(rl.error)
    return rl.output


if __name__ == "__main__":
    category = sys.argv[1] if len(sys.argv) > 1 else "test"
    print(json.dumps(run(category), indent=2, ensure_ascii=False))
