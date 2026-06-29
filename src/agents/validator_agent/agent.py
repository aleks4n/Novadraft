"""Validator agent — the subgraph wiring the validator's steps into one workflow."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from pydantic_core import to_jsonable_python

from langgraph.graph import END, START, StateGraph

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.agents.validator_agent.state import ValidatorState
from src.agents.validator_agent.subagents.query_gen.agent import generate_queries
from src.agents.validator_agent.tools.search import search
from src.agents.validator_agent.tools.rrf import rrf
from src.agents.validator_agent.subagents.screen.agent import screen
from src.agents.validator_agent.subagents.deep_read.agent import deep_read
from src.agents.validator_agent.subagents.verdict.agent import verdict


def build_validator_graph():
    """Wire the validator steps into a compiled graph.

    Flow: query_gen -> search -> rrf -> screen -> deep_read -> verdict.
    """
    g = StateGraph(ValidatorState)
    g.add_node("query_gen", generate_queries)
    g.add_node("search", search)
    g.add_node("rrf", rrf)
    g.add_node("screen", screen)
    g.add_node("deep_read", deep_read)
    g.add_node("verdict", verdict)

    g.add_edge(START, "query_gen")
    g.add_edge("query_gen", "search")
    g.add_edge("search", "rrf")
    g.add_edge("rrf", "screen")
    g.add_edge("screen", "deep_read")
    g.add_edge("deep_read", "verdict")
    g.add_edge("verdict", END)
    return g.compile()


def _readable_clause(concepts) -> str:
    """Human-readable OR/AND view of a query's concept groups (no arXiv `all:` prefix)."""
    return " AND ".join(
        f"({' OR '.join(group)})" if len(group) > 1 else group[0] for group in concepts
    )


def _slugify(text: str) -> str:
    """Make a folder name from a human-readable gap title."""
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")


def _preview(value) -> str:
    """Summary of a step's output, for the streaming runner below."""
    if hasattr(value, "queries"):  # SearchQueries
        return "".join(
            f"\n      {q.query_type}: {_readable_clause(q.concepts)}" for q in value.queries
        )
    if hasattr(value, "status") and hasattr(value, "confidence"):  # Verdict
        return f"{value.status} (confidence {value.confidence:.2f}, {len(value.assessments)} papers)"
    if isinstance(value, list) and value and isinstance(value[0], list):  # search_results
        return " / ".join(f"{len(sub)} papers" for sub in value)
    if isinstance(value, list) and value and hasattr(value[0], "relevance_to_gap"):  # assessments
        return f"{len(value)}: " + ", ".join(a.relevance_to_gap for a in value)
    if isinstance(value, list) and value and isinstance(value[0], str):  # skipped_ids
        return ", ".join(value)
    if isinstance(value, list):  # candidates / shortlist
        return f"{len(value)} items"
    return repr(value)[:80]


if __name__ == "__main__":
    import time
    from datetime import datetime, timezone

    from src.common.schemas.gap import GapRecord
    from src.common.utils import storage

    # CLI args: <gap_id> [category_slug]. Loads a real GapRecord from the workspace
    # (gaps/pending), the same place production gaps live — so the getters resolve
    # keywords + year against that category's papers.
    gap_id = sys.argv[1] if len(sys.argv) > 1 else "gap_9f0f24c1"
    category_slug = sys.argv[2] if len(sys.argv) > 2 else "test"
    gap_path = storage.category_dir(category_slug) / "gaps" / "pending" / f"{gap_id}.json"
    gap = GapRecord.model_validate_json(gap_path.read_text(encoding="utf-8"))

    base_name = f"{gap_path.stem}-{_slugify(gap.title)[:20]}"
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    run_dir = Path("src/agents/validator_agent/test_runs") / f"{base_name}-{timestamp}"
    run_dir.mkdir(parents=True, exist_ok=True)

    graph = build_validator_graph()
    print(f"GAP: {gap.title}\n")
    print(f"TRACE_DIR: {run_dir}\n")

    step_counter = 0
    last = time.perf_counter()
    for chunk in graph.stream({"gap": gap, "category_slug": category_slug}, stream_mode="updates"):
        for node, update in chunk.items():
            step_counter += 1
            (run_dir / f"step_{step_counter:02d}_{node}.json").write_text(
                json.dumps({"node": node, "state_update": to_jsonable_python(update)}, indent=2),
                encoding="utf-8",
            )

            now = time.perf_counter()
            for key, value in update.items():
                print(f"[{node}]  {now - last:.1f}s  {key}: {_preview(value)}")
            last = now

    verdict_path = run_dir / f"step_{step_counter:02d}_verdict.json"
    if verdict_path.exists():
        v = json.loads(verdict_path.read_text(encoding="utf-8"))["state_update"]["verdict"]
        print(f"\nVERDICT: {v['status']} (confidence {v['confidence']:.2f})")
        print(f"REASONING: {v['reasoning']}")
