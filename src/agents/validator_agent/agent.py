"""Validator agent — wires the steps into one graph, plus the standard `run()`
entrypoint that streams through GraphRunLog, attaches the verdict to the gap, and
routes it onward: solved gaps to the master list, opportunities to the ranking inbox.

Flow: query_gen -> search -> rrf -> screen -> deep_read -> verdict -> finalize.
Embeds as a subgraph node in the main pipeline; also runnable standalone
(`python src/agents/validator_agent/agent.py <gap_id> [category_slug]`).
"""

from __future__ import annotations

import sys
from pathlib import Path

from langgraph.graph import END, START, StateGraph

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.common.schemas.gap import GapRecord
from src.common.utils import storage
from src.common.utils.logging.runlog import GraphRunLog
from src.agents.validator_agent.state import ValidatorState
from src.agents.validator_agent.subagents.query_gen.agent import generate_queries
from src.agents.validator_agent.tools.search import search
from src.agents.validator_agent.tools.rrf import rrf
from src.agents.validator_agent.subagents.screen.agent import screen
from src.agents.validator_agent.subagents.deep_read.agent import deep_read
from src.agents.validator_agent.subagents.verdict.agent import verdict

def finalize(state: ValidatorState) -> dict:
    """Attach the verdict to the gap and route it out of the to_validate/ inbox:
    a *solved* gap is terminal (parked for dedup, not an opportunity) -> gaps/master/;
    an open / partially-solved gap goes to gaps/to_rank/ for the ranking agent.
    Side-effect only; no state change."""
    category = state["category_slug"]
    verdict = state["verdict"]
    gap = state["gap"].model_copy(update={"verdict": verdict})
    stage = "master" if verdict.status == "solved" else "to_rank"
    storage.write_gap_json(category, gap.model_dump(mode="json"), stage)
    storage.remove_gap_json(category, gap.gap_id, "to_validate")
    return {}


def _read_result(category_slug: str, gap_id: str) -> dict | None:
    """The finalized gap lands in master/ (solved) or to_rank/ (to be ranked)."""
    return (storage.read_gap_json(category_slug, gap_id, "master")
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
    return storage.read_gap_json(category_slug, gap_id, "master")


if __name__ == "__main__":
    import json

    # CLI args: <gap_id> [category_slug]. Loads the gap from gaps/to_validate/ and
    # validates it; the gap (verdict attached) lands in gaps/master/ (solved) or
    # gaps/to_rank/ (open/partial), plus a run log in logs/validator_agent/<run>.jsonl.
    gap_id = sys.argv[1] if len(sys.argv) > 1 else "gap_9f0f24c1"
    category = sys.argv[2] if len(sys.argv) > 2 else "test"
    record = run(category, gap_id)
    print(json.dumps(record, indent=2, ensure_ascii=False))
