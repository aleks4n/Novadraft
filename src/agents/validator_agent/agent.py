"""Validator agent — wires the steps into one graph, plus the standard `run()`
entrypoint that streams through GraphRunLog, attaches the verdict to the gap, and
routes it onward: solved gaps to the master list, opportunities to the ranking inbox.

Flow: query_gen -> search -> rrf -> screen -> deep_read -> verdict -> finalize.
Embeds as a subgraph node in the main pipeline; also runnable standalone
(`python src/agents/validator_agent/agent.py <gap_id> [category_slug]`).
"""

from __future__ import annotations

import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from langgraph.graph import END, START, StateGraph

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.common.schemas.gap import GapRecord
from src.common.utils import claims, storage
from src.common.utils.logging.runlog import GraphRunLog
from src.agents.validator_agent.state import ValidatorState
from src.agents.validator_agent.subagents.query_gen.agent import generate_queries
from src.agents.validator_agent.tools.search import search
from src.agents.validator_agent.tools.rrf import rrf
from src.agents.validator_agent.subagents.screen.agent import screen
from src.agents.validator_agent.subagents.deep_read.agent import deep_read
from src.agents.validator_agent.subagents.verdict.agent import verdict

def finalize(state: ValidatorState) -> dict:
    """Attach the verdict to the gap, advance its status past "unchecked", and
    route it out of the to_validate/ inbox: a *solved* gap is terminal (already
    addressed by prior art, not an opportunity) -> gaps/archive/, alongside
    duplicates; an open / partially-solved gap goes to gaps/to_rank/ for the
    ranking agent. This keeps gaps/master/ as the list of live, ranked
    opportunities only. Side-effect only; no state change."""
    category = state["category_slug"]
    verdict = state["verdict"]
    gap = state["gap"].model_copy(update={"verdict": verdict, "status": "validated"})
    stage = "archive" if verdict.status == "solved" else "to_rank"
    storage.write_gap_json(category, gap.model_dump(mode="json"), stage)
    storage.remove_gap_json(category, gap.gap_id, "to_validate")
    return {}


def _read_result(category_slug: str, gap_id: str) -> dict | None:
    """The finalized gap lands in archive/ (solved) or to_rank/ (to be ranked)."""
    return (storage.read_gap_json(category_slug, gap_id, "archive")
            or storage.read_gap_json(category_slug, gap_id, "to_rank"))


def build_validator_graph():
    """Wire the validator steps into a compiled graph.

    Flow: query_gen -> search -> rrf -> screen -> deep_read -> verdict -> finalize.
    """
    g = StateGraph(ValidatorState)
    g.add_node("query_gen", generate_queries)
    g.add_node("search", search)
    g.add_node("rrf", rrf)
    g.add_node("screen", screen)
    g.add_node("deep_read", deep_read)
    g.add_node("verdict", verdict)
    g.add_node("finalize", finalize)

    g.add_edge(START, "query_gen")
    g.add_edge("query_gen", "search")
    g.add_edge("search", "rrf")
    g.add_edge("rrf", "screen")
    g.add_edge("screen", "deep_read")
    g.add_edge("deep_read", "verdict")
    g.add_edge("verdict", "finalize")
    g.add_edge("finalize", END)
    return g.compile()


_GRAPH = None


def _get_graph():
    global _GRAPH
    if _GRAPH is None:
        _GRAPH = build_validator_graph()
    return _GRAPH


def _load_gap(category_slug: str, gap_id: str) -> GapRecord:
    """Load a gap awaiting validation from gaps/to_validate/<gap_id>.json."""
    storage.gap_stage_dir(category_slug, "to_validate").mkdir(parents=True, exist_ok=True)
    raw = storage.read_gap_json(category_slug, gap_id, "to_validate")
    if raw is None:
        inbox = storage.gap_stage_dir(category_slug, "to_validate")
        raise FileNotFoundError(f"Gap '{gap_id}' not found in {inbox}")
    return GapRecord.model_validate(raw)


def run(category_slug: str, gap_id: str, *, stream: bool = True,
        echo: bool | None = None) -> dict | None:
    """Validate one gap: search prior art published after its cutoff and judge
    whether it has since been addressed. Attaches the resulting Verdict to the
    gap, moves it from gaps/to_validate/ to gaps/master/ (solved) or gaps/to_rank/
    (open / partially-solved), and returns the updated gap record (with ``.verdict``).
    Standalone-callable.

    By default streams the graph through a GraphRunLog, writing the standard
    per-run ``.jsonl`` to ``workspace/<cat>/logs/validator_agent/`` and echoing
    progress to the terminal on a TTY. Pass ``stream=False`` for a quiet,
    log-free ``.invoke()``.
    """
    graph = _get_graph()
    inputs = {"gap": _load_gap(category_slug, gap_id), "category_slug": category_slug}

    if not stream:
        graph.invoke(inputs)
        return _read_result(category_slug, gap_id)

    if echo is None:
        echo = sys.stderr.isatty()
    rl = GraphRunLog("validator_agent", workspace=category_slug, run_hint=gap_id)
    rl.run(graph, inputs, echo=echo)
    if rl.error:
        raise RuntimeError(rl.error)
    return _read_result(category_slug, gap_id)


_MAX_PARALLEL_GAPS = 3  # concurrent gap validations
_validate_all_lock = threading.Lock()  # one validate_all batch at a time; a second call queues


def validate_all(category_slug: str) -> dict:
    """Validate every gap waiting in gaps/to_validate/, up to _MAX_PARALLEL_GAPS at a time.

    Contract with the director: drop the whole batch of novel gaps into to_validate/,
    then call this once. The inbox is listed once, AFTER acquiring the module lock,
    and each gap is then *claimed* in the process-global ``claims`` registry — so
    overlapping invocations queue instead of racing, and a gap another run (e.g. a
    live pipeline ingest in the app) is already processing is skipped entirely.

    One gap failing doesn't stop the others: its error is caught and reported, and the
    gap simply stays in to_validate/ (it will be retried on the next invocation). Prints
    one line per finished gap; the per-run detail still lands in the standard
    logs/validator_agent/<run>.jsonl files (terminal echo is off to avoid interleaving).

    Returns {"validated": {gap_id: gap record}, "failed": {gap_id: error message}}.
    """
    with _validate_all_lock:
        inbox = storage.list_gap_ids(category_slug, "to_validate")  # list after locking
        gap_ids = claims.claim_batch(category_slug, inbox)  # skip gaps another run holds
        if not gap_ids:
            print(f"to_validate/ of '{category_slug}' is empty (or fully claimed by "
                  "other runs) — nothing to validate.")
            return {"validated": {}, "failed": {}}
        _get_graph()  # build once here, not lazily from three threads at once

        print(f"validating {len(gap_ids)} gap(s), up to {_MAX_PARALLEL_GAPS} in parallel ...")
        validated: dict[str, dict | None] = {}
        failed: dict[str, str] = {}

        def _one(gap_id: str) -> None:
            t0 = time.monotonic()
            try:
                record = run(category_slug, gap_id, stream=True, echo=False)
                status = ((record or {}).get("verdict") or {}).get("status", "?")
                print(f"  {gap_id} -> {status}  ({time.monotonic() - t0:.0f}s)")
                validated[gap_id] = record
            except Exception as exc:  # noqa: BLE001 — gap stays in to_validate/ for a retry
                print(f"  {gap_id} -> FAILED after {time.monotonic() - t0:.0f}s: {exc}")
                failed[gap_id] = str(exc)
            finally:
                claims.release(category_slug, gap_id)

        try:
            with ThreadPoolExecutor(max_workers=_MAX_PARALLEL_GAPS) as pool:
                list(pool.map(_one, gap_ids))
        finally:
            claims.release_all(category_slug, gap_ids)  # backstop — no leaked claims

        summary = f"done: {len(validated)} validated, {len(failed)} failed"
        print(summary + (" — failed gaps stay in to_validate/" if failed else ""))
        return {"validated": validated, "failed": failed}


if __name__ == "__main__":
    import json

    # CLI: <gap_id> [category_slug]  validate one gap (streams progress to the terminal)
    #      --all [category_slug]     validate everything in gaps/to_validate/, in parallel
    # Validated gaps land in gaps/master/ (solved) or gaps/to_rank/ (open/partial), plus
    # a run log in logs/validator_agent/<run>.jsonl.
    arg = sys.argv[1] if len(sys.argv) > 1 else "gap_9f0f24c1"
    category = sys.argv[2] if len(sys.argv) > 2 else "test"
    if arg == "--all":
        validate_all(category)
    else:
        record = run(category, arg)
        print(json.dumps(record, indent=2, ensure_ascii=False))
