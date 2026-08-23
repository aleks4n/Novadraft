"""score_openness: maps the gap's validator verdict to an openness score in [0, 1].

High validator confidence raises openness for `open` gaps (we're sure it's untouched) but
lowers it for `partially_solved` gaps (we're sure real progress exists). Solved gaps never
reach the ranking agent — the validator parks them in master/.
"""

from __future__ import annotations

from typing import Optional

from src.common.schemas.verdict import Verdict
from src.agents.ranking_agent.state import RankingState

# openness is in [0, 1]:  openness = base +/- slope * certainty
_OPEN_BASE = 0.75
_OPEN_SLOPE = 0.25  # open    -> [0.50, 1.00]
_PARTIAL_BASE = 0.35
_PARTIAL_SLOPE = 0.25  # partial -> [0.10, 0.60]


def _openness(verdict: Verdict) -> Optional[float]:
    """status + confidence -> openness in [0, 1]; None if not an opportunity (solved)."""
    certainty = 2.0 * verdict.confidence - 1.0
    if verdict.status == "open":
        score = _OPEN_BASE + _OPEN_SLOPE * certainty
    elif verdict.status == "partially_solved":
        score = _PARTIAL_BASE - _PARTIAL_SLOPE * certainty
    else:  # "solved"
        return None
    return max(0.0, min(1.0, score))


def score_openness(state: RankingState) -> dict:
    """LangGraph node: gap -> {"openness": score}.

    to_rank/ only ever holds open / partially-solved gaps (the validator routes solved gaps
    straight to master/), so a missing or solved verdict here is a contract violation — fail
    loud rather than write a meaningless ranking.
    """
    gap = state["gap"]
    score = _openness(gap.verdict) if gap.verdict else None
    if score is None:
        raise ValueError(f"ranking received a gap without an open/partial verdict: {gap.gap_id}")
    return {"openness": score}
