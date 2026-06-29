"""The ranking subgraph's state — what flows between its nodes.

A LangGraph TypedDict; `total=False` so each node returns only the keys it sets.
Inputs (gaps, verdicts, config) are populated at the boundary — the upstream
validator in the orchestrated pipeline, or the runner for a standalone run. Each
score_* node writes its own per-dimension dict keyed by gap_id; a gap missing from
a dict was filtered out, so the next node only sees survivors.

Flow: (gaps, verdicts) -> openness -> impact -> feasibility -> ranked (top-X).
"""

from __future__ import annotations

from typing import Dict, List, TypedDict

from src.common.schemas.gap import Gap
from src.common.schemas.verdict import Verdict
from src.agents.ranking_agent.schemas import (
    FeasibilityAssessment,
    ImpactBreakdown,
    RankedGap,
    RankingConfig,
)


class RankingState(TypedDict, total=False):
    config: RankingConfig  # input: tunable knobs (filters + weights)
    gaps: List[Gap]  # input: the master gap list
    verdicts: Dict[str, Verdict]  # input: gap_id -> validator Verdict

    openness: Dict[str, float]  # after score_openness (survivors only)
    impact: Dict[str, ImpactBreakdown]  # after score_impact (score + sub-components)
    feasibility: Dict[str, FeasibilityAssessment]  # after score_feasibility

    ranked: List[RankedGap]  # output: top-X ranked gaps
