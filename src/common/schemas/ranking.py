"""Ranking agent output: the per-gap dimension scores attached to a gap.

Holds the three raw dimension scores but not a combined final score: the final
ordering is a weighted geometric mean of the three, and the weights are
user-tunable at view time, so re-weighting never requires re-scoring. Only open /
partially-solved gaps are ranked; solved gaps carry no ``RankingScores``.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class RankingScores(BaseModel):
    """The three ranking dimensions for one gap, each in [0, 1]."""

    openness: float = Field(ge=0.0, le=1.0)  # how open/untouched the gap still is
    impact: float = Field(ge=0.0, le=1.0)  # venue/recency/evidence-based importance
    feasibility: float = Field(ge=0.0, le=1.0)  # how tractable making progress is
    feasibility_reasoning: str = ""  # short LLM justification for the feasibility score
