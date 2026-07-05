"""The ranking subgraph's state — what flows between its nodes.

A LangGraph TypedDict; `total=False` so each node returns only the keys it sets. The inputs
(gap, category_slug, config) are populated at the boundary — `run()` for a standalone run, or
the orchestrator in the full pipeline. The subgraph scores ONE gap; `finalize` attaches a
RankingScores to it and moves it to master/.

Flow: gap -> openness -> impact -> feasibility -> finalize (-> master/).
"""

from __future__ import annotations

from typing import TypedDict

from src.common.schemas.gap import GapRecord
from src.agents.ranking_agent.schemas import (
    FeasibilityAssessment,
    ImpactBreakdown,
    RankingConfig,
)


class RankingState(TypedDict, total=False):
    config: RankingConfig  # input: impact-internal knobs (compute-time; see schemas.py)
    category_slug: str  # input: workspace partition, for paper.json getters (venue, year)
    gap: GapRecord  # input: one open / partially-solved gap (carries its validator verdict)

    openness: float  # after score_openness
    impact: ImpactBreakdown  # after score_impact (score + sub-components)
    feasibility: FeasibilityAssessment  # after score_feasibility
