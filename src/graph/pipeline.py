"""Ingestion pipeline — one LangGraph that runs a paper end-to-end:

    START → paper → [gaps? → director]
          → enqueue → validate_gap ⟳ → enqueue_rank → rank_gap ⟳ → collect → END

- ``paper``  — the paper_agent subgraph (fetch → extract → summarize → gap),
  mounted directly as a node so its internal stages stream/expand inline. Writes
  ``papers/<id>/paper.json`` and one ``gaps/pending/<gap_id>.json`` per gap.
- ``director`` — the director_agent subgraph. Dedups the pending gaps against the
  workspace's known gaps: duplicates are merged + archived, novel gaps move to
  ``gaps/to_validate/`` and are exposed as ``novel_gap_ids``.
- ``enqueue`` → ``validate_gap`` — ``enqueue`` *claims* this run's novel gaps via
  the process-global ``claims`` registry (so a concurrent run sweeping the same
  folder skips them) and seeds the queue; ``validate_gap`` then drains the whole
  queue with a bounded thread pool (``_MAX_PARALLEL_GAPS`` at a time), invoking
  the validator subgraph per gap (which routes it to ``gaps/master/`` if solved
  or ``gaps/to_rank/`` if still open). Parallelism is safe because every arXiv
  call site goes through the shared ``ratelimit`` gate — requests stay spaced
  across threads and across concurrent runs. Per-gap progress is emitted as
  ``gap_validate_start`` / ``gap_validated`` trace events so the UI can tick
  gaps individually.
- ``enqueue_rank`` → ``rank_gap`` — the ranking agent then scores each open
  opportunity the validator left in ``gaps/to_rank/`` (openness / impact /
  feasibility) and moves it into ``gaps/master/``. Same claim-then-drain
  bounded-parallel pattern, scoped to this run's gaps.
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
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from pathlib import Path

from langgraph.graph import END, START, StateGraph

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.common.schemas.gap import GapRecord
from src.common.utils import claims, storage
from src.common.utils.logging import trace
from src.common.utils.logging.runlog import GraphRunLog
from src.agents.paper_agent.agent import build_paper_graph
from src.agents.director_agent.agent import build_director_graph
from src.agents.validator_agent.agent import build_validator_graph
from src.agents.ranking_agent.agent import build_ranking_graph
from src.agents.ranking_agent.schemas import RankingConfig
from src.graph.state import PipelineState

# Worst case: the paper subgraph (~fetch/extract + two bounded extractor↔critic
# cycles) + the director's nodes + the validate/rank phases + collect. The
# validate/rank nodes each drain their whole queue in one super-step (bounded
# thread pool inside the node), and the validator/ranking subgraphs run via
# .invoke() with their own independent recursion budgets — so the outer
# super-step count no longer grows with the gap count. Headroom kept anyway.
_RECURSION_LIMIT = 300

# Concurrent gap validations / rankings within one run. Bounded on purpose:
# external calls stay safe via the shared ratelimit gate, but each gap is a
# full subgraph run with several LLM calls — keep the fan-out modest.
_MAX_PARALLEL_GAPS = 3

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


def _read_validated_gap(slug: str, gap_id: str) -> dict | None:
    """Read either terminal branch produced by the validator.

    Open/partial gaps move to ``to_rank``; solved gaps move to ``archive``.
    ``master`` is retained as a compatibility fallback for older workspaces.
    """
    return (storage.read_gap_json(slug, gap_id, "to_rank")
            or storage.read_gap_json(slug, gap_id, "archive")
            or storage.read_gap_json(slug, gap_id, "master"))


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


# --- parallel drain helper ----------------------------------------------------

def _drain_parallel(slug: str, queue: list[str], worker, phase: str) -> tuple[list[dict], list[str]]:
    """Process every claimed gap in ``queue`` with a bounded thread pool and
    return the finalized records (order of completion is irrelevant — the
    reducer just accumulates them).

    Each task runs under ``copy_context()`` so LangGraph's stream writer (and
    the current node tag) is visible from the worker threads — the per-gap trace
    events and every LLM call inside the subgraphs keep landing in the run log.
    One gap failing doesn't stop the others: the error is narrated as a trace
    event and the gap stays in its stage folder for a later retry. Claims are
    released per gap in ``finally`` — success or failure — so a crashed gap is
    immediately claimable again."""

    def _one(gid: str) -> tuple[str, dict | None]:
        try:
            return gid, worker(gid)
        except Exception as exc:  # noqa: BLE001 — gap stays put for a retry
            trace.log_event(f"gap_{phase}_failed", gap_id=gid, error=str(exc))
            return gid, None
        finally:
            claims.release(slug, gid)

    try:
        with ThreadPoolExecutor(max_workers=_MAX_PARALLEL_GAPS) as pool:
            futures = [pool.submit(copy_context().run, _one, gid) for gid in queue]
            outcomes = [f.result() for f in futures]
            return ([rec for _, rec in outcomes if rec is not None],
                    [gid for gid, rec in outcomes if rec is None])
    finally:
        # Backstop: if the pool itself blew up, don't leak the batch's claims.
        claims.release_all(slug, queue)


# --- phase 1: validate (claim, then drain in bounded parallel) -----------------

def enqueue(state: PipelineState) -> dict:
    """Claim + seed the validation queue with this run's novel gaps (the ones the
    director just moved into to_validate). Gaps already claimed by a concurrent
    run are skipped — they're being handled."""
    slug = state["category_slug"]
    inbox = _in_validate_inbox(slug, state.get("novel_gap_ids") or [])
    return {"queue": claims.claim_batch(slug, inbox)}


def _next_validate(state: PipelineState) -> str:
    """Anything queued → validate it; else move on to ranking. (The validate node
    drains the whole queue in one step, so this fires at most once per phase.)"""
    return "validate_gap" if (state.get("queue") or []) else "enqueue_rank"


def validate_gap(state: PipelineState) -> dict:
    """Validate every queued gap, up to ``_MAX_PARALLEL_GAPS`` at a time. Each gap
    runs the validator subgraph (which persists the verdict and moves the gap to
    master/ or to_rank/); parallel arXiv traffic stays spaced by the shared
    ``ratelimit`` gate. Emits ``gap_validate_start`` / ``gap_validated`` trace
    events so the UI can show per-gap progress."""
    queue = list(state.get("queue") or [])
    if not queue:
        return {}
    slug = state["category_slug"]

    def _validate(gid: str) -> dict | None:
        raw = storage.read_gap_json(slug, gid, "to_validate")
        if raw is None:
            return None
        trace.log_event("gap_validate_start", gap_id=gid)
        _validator().invoke({"gap": GapRecord.model_validate(raw), "category_slug": slug})
        rec = _read_validated_gap(slug, gid)
        trace.log_event("gap_validated", gap_id=gid,
                        status=((rec or {}).get("verdict") or {}).get("status"))
        return rec

    verdicts, failed = _drain_parallel(slug, queue, _validate, "validate")
    return {"queue": [], "verdicts": verdicts, "failed_validation_ids": failed}


# --- phase 2: rank the open opportunities (claim, then drain in parallel) ------

def enqueue_rank(state: PipelineState) -> dict:
    """Claim + seed the ranking queue with this run's open/partial gaps — the ones
    the validator left in to_rank/ (scoped to this paper / gap)."""
    slug = state["category_slug"]
    scope = _scope_ids(state)
    inbox = storage.list_gap_ids(slug, "to_rank")
    return {"queue": claims.claim_batch(slug, [g for g in inbox if (scope is None or g in scope)])}


def _next_rank(state: PipelineState) -> str:
    """Anything queued → rank it; else finish."""
    return "rank_gap" if (state.get("queue") or []) else "collect"


def rank_gap(state: PipelineState) -> dict:
    """Rank every queued opportunity, up to ``_MAX_PARALLEL_GAPS`` at a time. Each
    gap runs the ranking subgraph (which scores it on openness / impact /
    feasibility and moves it from to_rank/ into master/). Emits
    ``gap_rank_start`` / ``gap_ranked`` trace events for per-gap progress."""
    queue = list(state.get("queue") or [])
    if not queue:
        return {}
    slug = state["category_slug"]

    def _rank(gid: str) -> dict | None:
        raw = storage.read_gap_json(slug, gid, "to_rank")
        if raw is None:
            return None
        trace.log_event("gap_rank_start", gap_id=gid)
        _ranking().invoke({"gap": GapRecord.model_validate(raw), "category_slug": slug,
                           "config": RankingConfig()})
        rec = storage.read_gap_json(slug, gid, "master")
        trace.log_event("gap_ranked", gap_id=gid)
        return rec

    ranked, failed = _drain_parallel(slug, queue, _rank, "rank")
    return {"queue": [], "ranked": ranked, "failed_ranking_ids": failed}


def collect(state: PipelineState) -> dict:
    verdicts = [v for v in (state.get("verdicts") or []) if v]
    ranked = [r for r in (state.get("ranked") or []) if r]
    failed_validation_ids = state.get("failed_validation_ids") or []
    failed_ranking_ids = state.get("failed_ranking_ids") or []
    incomplete = len(failed_validation_ids) + len(failed_ranking_ids)
    return {"result": {
        "status": "partial" if incomplete else "ok",
        "error": (
            f"{incomplete} gap(s) could not finish ingestion; use Resume gaps to retry."
            if incomplete else None
        ),
        "paper_id": state.get("paper_id"),
        "novel_gap_ids": state.get("novel_gap_ids") or [],
        "n_validated": len(verdicts),
        "n_ranked": len(ranked),
        "verdicts": verdicts,
        "ranked": ranked,
        "failed_validation_ids": failed_validation_ids,
        "failed_ranking_ids": failed_ranking_ids,
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
    """Claim + seed the validation queue for a resume: the gaps awaiting
    validation (in to_validate/) that fall in this run's scope (a gap / a paper /
    all). Gaps a concurrent run already claimed are skipped."""
    slug = state["category_slug"]
    scope = set(state.get("scope_gap_ids") or [])
    inbox = storage.list_gap_ids(slug, "to_validate")
    return {"queue": claims.claim_batch(slug, [g for g in inbox if (not scope or g in scope)])}


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
    """Compile the full paper → director → validator ingestion graph. The validate
    and rank phases each claim their gaps, then drain them in bounded parallel
    (``_MAX_PARALLEL_GAPS`` at a time) inside one node step."""
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
