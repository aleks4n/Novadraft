"""Ranking-agent-local schemas: the tunable config, the per-dimension scores, and
the LLM output for feasibility.

`RankingConfig` is the UI-facing knob set — filters gate which gaps survive, weights
decide how survivors are ordered. Scores are config-dependent and computed at rank
time; only raw facts live upstream (paper.json `metrics`, the validator `Verdict`).
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class RankingConfig(BaseModel):
    """Tunable ranking knobs — this schema doubles as the UI's knob list.

    The *compute-time* knobs (venue/citation blend, evidence bonus, recency) are the only ones
    the ranking agent uses. The *view-time* knobs — the three dimension weights and the min_*
    floors — are applied downstream by the UI when it computes the final weighted score and
    orders/filters; the agent itself does not read them. (No top_x: the agent scores and returns
    every gap; how many to show is a pure display choice.)
    """

    # --- view-time knobs: applied by the UI, NOT the agent (see docstring) ---
    # filter floors: the UI drops a gap that falls below any of these
    min_openness: float = Field(0.0, ge=0.0, le=1.0)
    min_impact: float = Field(0.0, ge=0.0, le=1.0)
    min_feasibility: float = Field(0.0, ge=0.0, le=1.0)
    # dimension weights: the UI's weighted geometric mean over the three scores
    w_openness: float = Field(1.0, ge=0.0)
    w_impact: float = Field(1.0, ge=0.0)
    w_feasibility: float = Field(1.0, ge=0.0)

    # --- compute-time knobs: used by score_impact ---
    # venue + citation quality blend; evidence is a separate bonus
    w_venue: float = Field(1.0, ge=0.0)
    w_citation: float = Field(1.0, ge=0.0)
    evidence_bonus: float = Field(
        0.3, ge=0.0, description="max multiplicative impact bonus from multiple evidence papers (1 paper = no bonus)"
    )
    # recency decay applied to impact: floor + (1-floor) * 0.5^(age_years / half_life)
    recency_half_life: float = Field(2.5, gt=0.0, description="years until the decay halves")
    recency_floor: float = Field(0.4, ge=0.0, le=1.0, description="never decay below this")
    # citation scoring: raw counts are unbounded and accumulate with age, so they are read
    # as a RATE (citations per year since publication) and log-compressed. Using a rate
    # rather than a total also keeps this from re-measuring what `recency` already covers.
    # 1000/year is landmark-paper territory (a top NeurIPS/ICLR paper runs 300-1500);
    # calibrated so strong venues separate rather than all clipping at the ceiling.
    citations_per_year_full: float = Field(
        1000.0, gt=0.0,
        description="citations/year that scores ~1.0; the scale is logarithmic below it",
    )


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
