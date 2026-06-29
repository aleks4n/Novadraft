"""combine (deterministic) — fuse the three dimension scores into the final ranking.

A gap that reached `feasibility` passed every filter, so it has all three scores. We
combine them with a **weighted geometric mean**, so a gap must be decent on *all three*
axes — a near-zero on any one tanks the final score (a brilliant but infeasible gap
shouldn't rank). Then sort and return the top-X RankedGaps.
"""

from __future__ import annotations

import math
from typing import List, Sequence, Tuple

from src.agents.ranking_agent.schemas import GapScore, RankedGap
from src.agents.ranking_agent.state import RankingState


def _weighted_geomean(pairs: Sequence[Tuple[float, float]]) -> float:
    """exp( sum(w*ln(v)) / sum(w) ) over (value, weight) pairs; 0 if any weighted value is 0."""
    num = 0.0
    wsum = 0.0
    for value, weight in pairs:
        if weight <= 0:
            continue
        if value <= 0:
            return 0.0  # zero on a weighted axis kills the gap
        num += weight * math.log(value)
        wsum += weight
    return math.exp(num / wsum) if wsum > 0 else 0.0


def combine(state: RankingState) -> dict:
    """LangGraph node: the three score dicts -> {"ranked": [RankedGap, ...]} (top-X)."""
    config = state["config"]
    openness = state.get("openness", {})
    impact = state.get("impact", {})
    feasibility = state.get("feasibility", {})
    gaps_by_id = {g.gap_id: g for g in state.get("gaps", [])}

    ranked: List[RankedGap] = []
    for gap_id, feas in feasibility.items():  # feasibility survivors passed all three filters
        gap = gaps_by_id.get(gap_id)
        o, impact_bd = openness.get(gap_id), impact.get(gap_id)
        if gap is None or o is None or impact_bd is None:
            continue  # defensive; shouldn't happen given the linear filtering
        i = impact_bd.score
        final = _weighted_geomean(
            [(o, config.w_openness), (i, config.w_impact), (feas.score, config.w_feasibility)]
        )
        ranked.append(
            RankedGap(
                gap=gap,
                score=GapScore(
                    openness=o,
                    impact=i,
                    feasibility=feas.score,
                    final_score=final,
                    feasibility_reasoning=feas.reasoning,
                ),
            )
        )

    ranked.sort(key=lambda r: r.score.final_score, reverse=True)
    return {"ranked": ranked[: config.top_x]}
