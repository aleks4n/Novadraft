"""Ingestion pipeline — one LangGraph that runs a paper end-to-end:

    START → paper → [gaps? → director]
          → enqueue → validate_gap ⟳ → enqueue_rank → rank_gap ⟳ → collect → END

- ``paper``  — the paper_agent subgraph (fetch → extract → summarize → gap),
  mounted directly as a node so its internal stages stream/expand inline. Writes
  ``papers/<id>/paper.json`` and one ``gaps/pending/<gap_id>.json`` per gap.
- ``director`` — the director_agent subgraph. Dedups the pending gaps against the
  workspace's known gaps: duplicates are merged + archived, novel gaps move to
  ``gaps/to_validate/`` and are exposed as ``novel_gap_ids``.
- ``enqueue`` → ``validate_gap`` — the validator runs **sequentially**, one gap at a
  time: ``enqueue`` seeds a queue of the novel gaps, then ``validate_gap`` pops the
  front, invokes the validator subgraph (which routes the gap to ``gaps/master/``
  if solved or ``gaps/to_rank/`` if still open), appends the record to ``verdicts``,
  and loops back until the queue drains. Sequential on purpose: running the
  validators in parallel hammers the arXiv prior-art search into HTTP 429s.
- ``enqueue_rank`` → ``rank_gap`` — the ranking agent then scores each open
  opportunity the validator left in ``gaps/to_rank/`` (openness / impact /
  feasibility) and moves it into ``gaps/master/``. Same sequential one-at-a-time
  loop, scoped to this run's gaps.
- ``collect`` — assembles the run's ``result``.

The validator is invoked with ``.invoke()`` inside ``validate_gap`` (its own
independent recursion budget), and the pipeline strip shows the validator as one
aggregate stage — the per-gap detail is read back from disk on the paper page.

``run(category_slug, query, force=False)`` streams the whole graph through the
shared ``GraphRunLog`` (same ``.jsonl`` the dev harness/app produce), so the app's
"Add paper" flow renders the entire pipeline live from one SSE stream.
"""
from __future__ import annotations

import sys
from pathlib import Path

from langgraph.graph import END, START, StateGraph

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.common.schemas.gap import GapRecord
from src.common.utils import storage
from src.common.utils.logging.runlog import GraphRunLog
from src.agents.paper_agent.agent import build_paper_graph
from src.agents.director_agent.agent import build_director_graph
from src.agents.validator_agent.agent import build_validator_graph
from src.agents.ranking_agent.agent import build_ranking_graph
from src.agents.ranking_agent.schemas import RankingConfig
from src.graph.state import PipelineState

# Worst case: the paper subgraph (~fetch/extract + two bounded extractor↔critic
# cycles) + the director's nodes + one super-step per gap in the sequential
# validate loop + collect. The validator runs via .invoke() with its own
# independent recursion budget, so only these outer super-steps count here — but
# the per-gap loop grows with the gap count, so keep plenty of headroom.
_RECURSION_LIMIT = 300

_VALIDATOR = None
_RANKING = None


def _validator():
    global _VALIDATOR
    if _VALIDATOR is None:
        _VALIDATOR = build_validator_graph()
    return _VALIDATOR


def _ranking():
    global _RANKING
    if _RANKING is None:
        _RANKING = build_ranking_graph()
    return _RANKING


def _route_after_paper(state: PipelineState) -> str:
    """Skip the rest of the pipeline when the paper short-circuited (ambiguous /
    needs a source) or produced no gaps."""
    if state.get("ambiguous") or state.get("needs_source"):
        return END
    gaps = state.get("gaps_result") or {}
    n = gaps.get("n_gaps")
    if n is None:
        n = len(gaps.get("gap_ids") or [])
    return "director" if n else END


def _in_validate_inbox(slug: str, ids: list[str]) -> list[str]:
    """Keep only the gap ids actually awaiting validation (in to_validate/),
    preserving order."""
    inbox = set(storage.list_gap_ids(slug, "to_validate"))
    return [g for g in ids if g in inbox]


def _paper_gap_ids(slug: str, paper_id: str) -> list[str]:
    rec = storage.read_paper_json(slug, paper_id) or {}
    return list((rec.get("gaps") or {}).get("gap_ids") or [])


def _scope_ids(state: PipelineState) -> set[str] | None:
    """The gap ids this run is scoped to — an explicit ``scope_gap_ids`` (resume),
    else the current ``paper_id``'s gaps (ingest), else ``None`` (no restriction).
    Keeps the validate + rank phases per-paper, not category-wide."""
    if state.get("scope_gap_ids"):
        return set(state["scope_gap_ids"])
    if state.get("paper_id"):
        return set(_paper_gap_ids(state["category_slug"], state["paper_id"]))
    return None


# --- phase 1: validate (one gap at a time) -----------------------------------

def enqueue(state: PipelineState) -> dict:
    """Seed the validation queue with this run's novel gaps (the ones the director
    just moved into to_validate)."""
    slug = state["category_slug"]
    return {"queue": _in_validate_inbox(slug, state.get("novel_gap_ids") or [])}


def _next_validate(state: PipelineState) -> str:
    """Loop control: another gap queued → validate it; else move on to ranking."""
    return "validate_gap" if (state.get("queue") or []) else "enqueue_rank"


def validate_gap(state: PipelineState) -> dict:
    """Validate the next queued gap **sequentially** (one at a time — running the
    validators in parallel hammers the arXiv prior-art search into HTTP 429s).
    Loads the gap from the to_validate inbox, runs the validator subgraph (which
    persists the verdict and moves the gap to master/ or to_rank/), records the
    finalized gap, and pops it off the queue so the loop advances."""
    queue = list(state.get("queue") or [])
    if not queue:
        return {}
    gid, rest = queue[0], queue[1:]
    slug = state["category_slug"]
    verdicts: list[dict] = []
    raw = storage.read_gap_json(slug, gid, "to_validate")
    if raw is not None:
        _validator().invoke({"gap": GapRecord.model_validate(raw), "category_slug": slug})
        rec = (storage.read_gap_json(slug, gid, "master")
               or storage.read_gap_json(slug, gid, "to_rank"))
        if rec is not None:
            verdicts = [rec]
    return {"queue": rest, "verdicts": verdicts}


# --- phase 2: rank the open opportunities (one gap at a time) -----------------

def enqueue_rank(state: PipelineState) -> dict:
    """Seed the ranking queue with this run's open/partial gaps — the ones the
    validator left in to_rank/ (scoped to this paper / gap)."""
    slug = state["category_slug"]
    scope = _scope_ids(state)
    inbox = storage.list_gap_ids(slug, "to_rank")
    return {"queue": [g for g in inbox if (scope is None or g in scope)]}


def _next_rank(state: PipelineState) -> str:
    """Loop control: another opportunity queued → rank it; else finish."""
    return "rank_gap" if (state.get("queue") or []) else "collect"


def rank_gap(state: PipelineState) -> dict:
    """Rank the next queued opportunity: run the ranking subgraph (which scores it
    on openness / impact / feasibility and moves it from to_rank/ into master/),
    record it, and pop it off the queue."""
    queue = list(state.get("queue") or [])
    if not queue:
        return {}
    gid, rest = queue[0], queue[1:]
    slug = state["category_slug"]
    ranked: list[dict] = []
    raw = storage.read_gap_json(slug, gid, "to_rank")
    if raw is not None:
        _ranking().invoke({"gap": GapRecord.model_validate(raw), "category_slug": slug,
                           "config": RankingConfig()})
        rec = storage.read_gap_json(slug, gid, "master")
        if rec is not None:
            ranked = [rec]
    return {"queue": rest, "ranked": ranked}


def collect(state: PipelineState) -> dict:
    verdicts = [v for v in (state.get("verdicts") or []) if v]
    ranked = [r for r in (state.get("ranked") or []) if r]
    return {"result": {
        "status": "ok",
        "paper_id": state.get("paper_id"),
        "novel_gap_ids": state.get("novel_gap_ids") or [],
        "n_validated": len(verdicts),
        "n_ranked": len(ranked),
        "verdicts": verdicts,
        "ranked": ranked,
    }}


# --- resume: run the director+validator+ranking tail for a scope of gaps -----
# Resume is expressed against the workspace itself: a gap's *stage folder* is the
# durable checkpoint, so we don't replay a paper — we just run the remaining tail
# for the gaps that haven't finished. ``plan`` resolves the target set; ``director``
# is a cheap no-op when nothing is in pending/; the loops process only the scoped
# gaps currently sitting in to_validate/ (validate) and to_rank/ (rank). A gap
# already in master/ or archive/ has nothing left to do.

def plan(state: PipelineState) -> dict:
    """Resolve which gaps this resume targets: a single ``gap_id``, a paper's
    ``gap_ids``, or (neither) the whole to_validate backlog (scope ``[]``)."""
    slug = state["category_slug"]
    if state.get("gap_id"):
        scope = [state["gap_id"]]
    elif state.get("paper_id"):
        scope = _paper_gap_ids(slug, state["paper_id"])
    else:
        scope = []
    return {"scope_gap_ids": scope}


def enqueue_resume(state: PipelineState) -> dict:
    """Seed the validation queue for a resume: the gaps awaiting validation
    (in to_validate/) that fall in this run's scope (a gap / a paper / all)."""
    slug = state["category_slug"]
    scope = set(state.get("scope_gap_ids") or [])
    inbox = storage.list_gap_ids(slug, "to_validate")
    return {"queue": [g for g in inbox if (not scope or g in scope)]}


def build_resume_graph():
    """Compile the resume tail:

        plan → director → enqueue → validate_gap ⟳ → enqueue_rank → rank_gap ⟳ → collect

    Reuses the same director / validate_gap / rank_gap / collect nodes as the full
    pipeline; only the entry (``plan``) and the validate-queue seed differ, so a
    resume renders as the same kind of streamed run — just without the paper stages."""
    g = StateGraph(PipelineState)
    g.add_node("plan", plan)
    g.add_node("director", build_director_graph())
    g.add_node("enqueue", enqueue_resume)
    g.add_node("validate_gap", validate_gap)
    g.add_node("enqueue_rank", enqueue_rank)
    g.add_node("rank_gap", rank_gap)
    g.add_node("collect", collect)

    g.add_edge(START, "plan")
    g.add_edge("plan", "director")
    g.add_edge("director", "enqueue")
    g.add_conditional_edges("enqueue", _next_validate, ["validate_gap", "enqueue_rank"])
    g.add_conditional_edges("validate_gap", _next_validate, ["validate_gap", "enqueue_rank"])
    g.add_conditional_edges("enqueue_rank", _next_rank, ["rank_gap", "collect"])
    g.add_conditional_edges("rank_gap", _next_rank, ["rank_gap", "collect"])
    g.add_edge("collect", END)
    return g.compile()


def build_pipeline_graph():
    """Compile the full paper → director → validator ingestion graph. The validator
    runs sequentially over the paper's novel gaps (enqueue → validate_gap loop)."""
    g = StateGraph(PipelineState)
    # Mount the paper + director agents as first-class subgraph nodes so their
    # internal stages expand in the diagram and stream nested under this run.
    g.add_node("paper", build_paper_graph())
    g.add_node("director", build_director_graph())
    g.add_node("enqueue", enqueue)
    g.add_node("validate_gap", validate_gap)
    g.add_node("enqueue_rank", enqueue_rank)
    g.add_node("rank_gap", rank_gap)
    g.add_node("collect", collect)

    g.add_edge(START, "paper")
    g.add_conditional_edges("paper", _route_after_paper, {"director": "director", END: END})
    g.add_edge("director", "enqueue")
    # phase 1: validate each novel gap; phase 2: rank the open opportunities.
    g.add_conditional_edges("enqueue", _next_validate, ["validate_gap", "enqueue_rank"])
    g.add_conditional_edges("validate_gap", _next_validate, ["validate_gap", "enqueue_rank"])
    g.add_conditional_edges("enqueue_rank", _next_rank, ["rank_gap", "collect"])
    g.add_conditional_edges("rank_gap", _next_rank, ["rank_gap", "collect"])
    g.add_edge("collect", END)
    return g.compile()


_GRAPH = None


def _get_graph():
    global _GRAPH
    if _GRAPH is None:
        _GRAPH = build_pipeline_graph()
    return _GRAPH


def run(category_slug: str, query: str, force: bool = False, *,
        stream: bool = True, echo: bool | None = None) -> dict:
    """Ingest one paper end-to-end (fetch → summarize → mine gaps → dedup →
    validate) into ``category_slug``. Returns the paper's short-circuit result if
    the fetch was ambiguous / needs a source, otherwise the pipeline summary.
    Standalone-callable.

    By default streams the graph through a :class:`GraphRunLog`, writing the
    standard per-run ``.jsonl`` to ``workspace/<slug>/logs/pipeline/`` and echoing
    to the terminal on a TTY. Pass ``stream=False`` for a quiet ``.invoke()``.
    """
    graph = _get_graph()
    inputs = {"category_slug": category_slug, "query": query, "force": force}
    if not stream:
        return graph.invoke(inputs, config={"recursion_limit": _RECURSION_LIMIT}).get("result")

    if echo is None:
        echo = sys.stderr.isatty()
    rl = GraphRunLog("pipeline", workspace=category_slug, run_hint=query,
                     recursion_limit=_RECURSION_LIMIT)
    rl.run(graph, inputs, echo=echo)
    if rl.error:
        raise RuntimeError(rl.error)
    return rl.output


_RESUME_GRAPH = None


def _get_resume_graph():
    global _RESUME_GRAPH
    if _RESUME_GRAPH is None:
        _RESUME_GRAPH = build_resume_graph()
    return _RESUME_GRAPH


def resume(category_slug: str, gap_id: str = "", paper_id: str = "", *,
           stream: bool = True, echo: bool | None = None) -> dict:
    """Resume gap ingestion (director + validator) from where it stopped, using
    the gaps' stage folders as the checkpoint. Scope it to one gap (``gap_id``),
    one paper's gaps (``paper_id``), or the whole to_validate backlog (neither).
    Returns the run summary. Standalone-callable.

    Streams through a :class:`GraphRunLog` to ``workspace/<slug>/logs/resume/`` by
    default; pass ``stream=False`` for a quiet ``.invoke()``."""
    graph = _get_resume_graph()
    inputs: dict = {"category_slug": category_slug}
    if gap_id:
        inputs["gap_id"] = gap_id
    if paper_id:
        inputs["paper_id"] = paper_id
    if not stream:
        return graph.invoke(inputs, config={"recursion_limit": _RECURSION_LIMIT}).get("result")

    if echo is None:
        echo = sys.stderr.isatty()
    rl = GraphRunLog("resume", workspace=category_slug, run_hint=(gap_id or paper_id or category_slug),
                     recursion_limit=_RECURSION_LIMIT)
    rl.run(graph, inputs, echo=echo)
    if rl.error:
        raise RuntimeError(rl.error)
    return rl.output


if __name__ == "__main__":
    import json

    category = sys.argv[1] if len(sys.argv) > 1 else "test"
    query = sys.argv[2] if len(sys.argv) > 2 else "1706.03762"
    print(json.dumps(run(category, query), indent=2, default=str))
