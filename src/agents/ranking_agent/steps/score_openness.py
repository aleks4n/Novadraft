"""score_openness: maps each gap to an openness score in [0, 1].

high confidence raises openness for `open` gaps (we're sure it's untouched),
but lowers it for `partially_solved` gaps (we're sure real progress exists), 
and `solved` gaps are dropped they're not research opportunities.
"""

from __future__ import annotations

from typing import Dict, Optional

from src.common.schemas.verdict import Verdict
from src.agents.ranking_agent.state import RankingState

# openness is in [0, 1]:  openness = base +/- slope * certainty
_OPEN_BASE = 0.75
_OPEN_SLOPE = 0.25  # open    -> [0.50, 1.00]
_PARTIAL_BASE = 0.35
_PARTIAL_SLOPE = 0.25  # partial -> [0.10, 0.60]


def _openness(verdict: Verdict) -> Optional[float]:
    """status + confidence -> openness in [0, 1]; None if the gap should be dropped (solved)."""
    certainty = 2.0 * verdict.confidence - 1.0
    if verdict.status == "open":
        score = _OPEN_BASE + _OPEN_SLOPE * certainty
    elif verdict.status == "partially_solved":
        score = _PARTIAL_BASE - _PARTIAL_SLOPE * certainty
    else:  # "solved" -> not a research opportunity
        return None
    return max(0.0, min(1.0, score))


def score_openness(state: RankingState) -> dict:
    """LangGraph node: (gaps, verdicts) -> {"openness": {gap_id: score}} for survivors."""
    config = state["config"]
    verdicts = state.get("verdicts", {})

    openness: Dict[str, float] = {}
    for gap in state.get("gaps", []):
        verdict = verdicts.get(gap.gap_id)
        if verdict is None:
            continue  # not validated -> can't judge openness -> skip
        score = _openness(verdict)
        if score is None or score < config.min_openness:
            continue  # solved, or below the floor
        openness[gap.gap_id] = score
    return {"openness": openness}
