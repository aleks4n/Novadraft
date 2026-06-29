"""Ranking-agent-local schemas: the tunable config, the per-dimension scores, and
the LLM output for feasibility.

`RankingConfig` is the UI-facing knob set — filters gate which gaps survive, weights
decide how survivors are ordered. Scores are config-dependent and computed at rank
time; only raw facts live upstream (paper.json `metrics`, the validator `Verdict`).
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field

from src.common.schemas.gap import Gap


class RankingConfig(BaseModel):
    """Tunable ranking knobs (exposed in the UI; sensible defaults here)."""

    # --- filter floors: a gap is dropped if it falls below any of these ---
    min_openness: float = Field(0.0, ge=0.0, le=1.0)
    min_impact: float = Field(0.0, ge=0.0, le=1.0)
    min_feasibility: float = Field(0.0, ge=0.0, le=1.0)

    # --- dimension weights: relative importance in the combined (geometric) score ---
    w_openness: float = Field(1.0, ge=0.0)
    w_impact: float = Field(1.0, ge=0.0)
    w_feasibility: float = Field(1.0, ge=0.0)

    # --- impact: venue + citation are the quality blend; evidence is a separate bonus ---
    w_venue: float = Field(1.0, ge=0.0)
    w_citation: float = Field(1.0, ge=0.0)
    evidence_bonus: float = Field(
        0.3, ge=0.0, description="max multiplicative impact bonus from multiple evidence papers (1 paper = no bonus)"
    )

    # --- recency decay applied to impact: floor + (1-floor) * 0.5^(age_years / half_life) ---
    recency_half_life: float = Field(2.5, gt=0.0, description="years until the decay halves")
    recency_floor: float = Field(0.4, ge=0.0, le=1.0, description="never decay below this")

    # --- output ---
    top_x: int = Field(10, ge=1, description="how many top gaps to return")


class FeasibilityAssessment(BaseModel):
    """score_feasibility LLM output: judgment only (the gap is attached in code).

    Same "model doesn't echo ids" principle as the validator's subagents.
    """

    score: float = Field(
        ge=0.0,
        le=1.0,
        description=(
            "How feasible is making real progress on this gap? 1.0 = a tractable, "
            "well-scoped extension; 0.0 = needs a fundamental breakthrough / unavailable "
            "data / infeasible compute. Weigh data availability, compute cost, "
            "methodological maturity, scope, and whether progress is measurable."
        ),
    )
    reasoning: str = Field(description="Short justification for the feasibility score.")


class ImpactBreakdown(BaseModel):
    """The impact score plus its sub-components — kept in state for analysis/tuning.

    `score` is the value used in ranking; the rest show how it was built:
    `impact = quality(venue, citation) * recency * evidence_bonus`.
    """

    score: float = Field(ge=0.0, le=1.0)
    quality: float  # venue+citation blend, or the neutral prior when neither is known
    venue_score: Optional[float] = None  # best CORE venue score across evidence papers
    citation: Optional[float] = None  # best citation percentile across evidence papers
    recency: float = 1.0
    evidence_bonus: float = 1.0
    venues: List[str] = Field(default_factory=list)  # resolved CORE acronyms (readability)


class GapScore(BaseModel):
    """Per-gap score breakdown — kept transparent so a UI can show *why* a gap ranked."""

    openness: float = Field(ge=0.0, le=1.0)
    impact: float = Field(ge=0.0, le=1.0)
    feasibility: float = Field(ge=0.0, le=1.0)
    final_score: float = Field(ge=0.0, le=1.0)  # weighted geometric mean of the three
    feasibility_reasoning: str = ""


class RankedGap(BaseModel):
    """An output item: a surviving gap plus its score breakdown (list order = rank)."""

    gap: Gap
    score: GapScore
