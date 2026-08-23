"""Ranking agent — scores one validated opportunity on three dimensions
(openness, impact, feasibility) and moves it into the master gap list.

Flow: score_openness -> score_impact -> score_feasibility -> finalize. Reads a gap the
validator left in gaps/to_rank/ (open / partially-solved, carrying its verdict) and writes it
into gaps/master/ with a RankingScores attached. The final weighted ordering is left to view
time (user-tunable dimension weights), so no final score is computed or stored here.

Public API:
- run(category_slug, gap_id): rank one gap (to_rank/ -> master/).
- rescore_impact(category_slug, gap_id): cheaply recompute ONLY impact for an already-ranked
  gap in master/, for the director to call after merging a corroborating paper — adding
  evidence changes impact but not openness or feasibility, so those are kept (no reasoner call).

Embeds as a node in the main pipeline; also runnable standalone
(`python src/agents/ranking_agent/agent.py [category_slug]` ranks everything in to_rank/).
"""

from __future__ import annotations

import sys
from pathlib import Path

from langgraph.graph import END, START, StateGraph

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.common.schemas.gap import GapRecord
from src.common.schemas.ranking import RankingScores
from src.common.utils import storage
from src.common.utils.logging.runlog import GraphRunLog
from src.agents.ranking_agent.schemas import RankingConfig
from src.agents.ranking_agent.state import RankingState
from src.agents.ranking_agent.tools.score_openness import score_openness
from src.agents.ranking_agent.tools.score_impact import compute_impact, score_impact
from src.agents.ranking_agent.subagents.score_feasibility.agent import score_feasibility


def finalize(state: RankingState) -> dict:
    """Attach a RankingScores (the three raw dimension scores, no final score) to the gap and
    move it from gaps/to_rank/ into gaps/master/. Side-effect only."""
    category = state["category_slug"]
    gap = state["gap"]
    scored = gap.model_copy(update={"ranking": RankingScores(
        openness=state["openness"],
        impact=state["impact"].score,
        feasibility=state["feasibility"].score,
        feasibility_reasoning=state["feasibility"].reasoning,
    )})
    storage.write_gap_json(category, scored.model_dump(mode="json"), "master")
    storage.remove_gap_json(category, gap.gap_id, "to_rank")
    return {}


def build_ranking_graph():
    """Wire the ranking steps into a compiled graph.

    Flow: score_openness -> score_impact -> score_feasibility -> finalize.
    """
    g = StateGraph(RankingState)
    g.add_node("score_openness", score_openness)
    g.add_node("score_impact", score_impact)
    g.add_node("score_feasibility", score_feasibility)
    g.add_node("finalize", finalize)

    g.add_edge(START, "score_openness")
    g.add_edge("score_openness", "score_impact")
    g.add_edge("score_impact", "score_feasibility")
    g.add_edge("score_feasibility", "finalize")
    g.add_edge("finalize", END)
    return g.compile()


_GRAPH = None


def _get_graph():
    global _GRAPH
    if _GRAPH is None:
        _GRAPH = build_ranking_graph()
    return _GRAPH


def _load_gap(category_slug: str, gap_id: str) -> GapRecord:
    """Load a gap awaiting ranking from gaps/to_rank/<gap_id>.json."""
    raw = storage.read_gap_json(category_slug, gap_id, "to_rank")
    if raw is None:
        inbox = storage.gap_stage_dir(category_slug, "to_rank")
        raise FileNotFoundError(f"Gap '{gap_id}' not found in {inbox}")
    return GapRecord.model_validate(raw)


def run(category_slug: str, gap_id: str, *, config: RankingConfig | None = None,
        stream: bool = True, echo: bool | None = None) -> dict | None:
    """Score one gap from gaps/to_rank/ on the three dimensions, attach a RankingScores, and
    move it into gaps/master/. Returns the updated gap record (with ``.ranking``).

    Streams through a GraphRunLog by default (per-run ``.jsonl`` in
    ``workspace/<cat>/logs/ranking_agent/``, TTY echo). ``stream=False`` -> quiet ``.invoke()``.
    """
    inputs = {"gap": _load_gap(category_slug, gap_id), "category_slug": category_slug,
              "config": config or RankingConfig()}
    graph = _get_graph()

    if not stream:
        graph.invoke(inputs)
    else:
        if echo is None:
            echo = sys.stderr.isatty()
        rl = GraphRunLog("ranking_agent", workspace=category_slug, run_hint=gap_id)
        rl.run(graph, inputs, echo=echo)
        if rl.error:
            raise RuntimeError(rl.error)
    return storage.read_gap_json(category_slug, gap_id, "master")


def rescore_impact(category_slug: str, gap_id: str, *,
                   config: RankingConfig | None = None) -> dict | None:
    """Recompute ONLY the impact dimension of an already-ranked gap in gaps/master/ and update
    its stored RankingScores in place. For the director to call after merging a corroborating
    paper into a ranked gap: adding evidence shifts impact (evidence bonus + the new paper's
    venue / recency) but not openness or feasibility, so those are kept and no reasoner call fires.

    Returns the updated gap dict, or None if the gap isn't a ranked gap in master/.
    """
    raw = storage.read_gap_json(category_slug, gap_id, "master")
    if raw is None:
        return None
    gap = GapRecord.model_validate(raw)
    if gap.ranking is None:
        return None  # not a ranked gap — nothing to recompute (master/ holds ranked gaps only)
    breakdown = compute_impact(gap, category_slug, config or RankingConfig())
    updated = gap.model_copy(
        update={"ranking": gap.ranking.model_copy(update={"impact": breakdown.score})}
    )
    storage.write_gap_json(category_slug, updated.model_dump(mode="json"), "master")
    return updated.model_dump(mode="json")


if __name__ == "__main__":
    # CLI: [category_slug] — ranks every gap currently in gaps/to_rank/ (each a separate
    # logged run) and moves it to gaps/master/.
    category = sys.argv[1] if len(sys.argv) > 1 else "test"
    ids = storage.list_gap_ids(category, "to_rank")
    for gid in ids:
        run(category, gid)
    print(f"ranked {len(ids)} gaps -> master/: {ids}")
