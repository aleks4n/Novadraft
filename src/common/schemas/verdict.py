"""Validator outputs: PaperAssessment + Verdict — the cross-agent contract.

Produced by the validator, consumed by the ranking agent (which reads the Verdict
from shared graph state). `PaperAssessment` is the per-paper judgment that backs
the verdict (renamed from the stage-specific "DeepReadNote" to be self-explanatory
to anyone reading common/).
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from src.common.schemas.gap import Gap
from src.common.schemas.paper import Paper

# How one paper relates to the gap (judged during Deep Read).
#   direct_solution  — directly & substantially solves the gap's specific problem
#   partial_solution — solves part of it, or only under limited conditions
#   not_addressed    — does not actually tackle the gap's problem (even if same field)
RelevanceToGap = Literal["direct_solution", "partial_solution", "not_addressed"]
# The verdict on the gap across the whole search.
VerdictStatus = Literal["open", "partially_solved", "solved"]


class PaperAssessment(BaseModel):
    """A structured judgment of one paper against the gap."""

    paper: Paper
    relevance_to_gap: RelevanceToGap
    contribution_summary: str  # what this paper does that touches the gap
    what_remains_open: str  # the part of the gap this paper does NOT address
    evidence_snippets: List[str] = Field(default_factory=list)  # verbatim quotes


class Verdict(BaseModel):
    """The validator's output for one gap."""

    gap_id: str  # links this verdict back to its Gap (many gaps flow through)
    status: VerdictStatus
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str
    assessments: List[PaperAssessment] = Field(default_factory=list)
    refined_gap: Optional[Gap] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
