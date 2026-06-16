from __future__ import annotations
from typing import List, Literal
from pydantic import BaseModel, Field

from src.common.schemas.verdict import RelevanceToGap, VerdictStatus


QueryType = Literal["literal", "reframed", "broad"]

class SearchQuery(BaseModel):
    query_type: QueryType
    concepts: List[List[str]] = Field(
        description=(
            "List of concept groups. Each inner list holds ONE concept's synonyms as "
            'SEPARATE strings, e.g. ["anomaly detection", "outlier detection"]. Do NOT '
            "write 'OR'/'AND' or join terms into one string — the search step ORs within "
            "a group and ANDs across groups. Include the anchor as one group."
        )
    )


class SearchQueries(BaseModel):
    """Query-gen output: the diverse set of queries the search step will run."""

    queries: List[SearchQuery]


class Screening(BaseModel):
    """Screening output: which candidate papers (positions not ids) to shortlist for deep read, and why."""

    selected: List[int] = Field(
        description=(
            "Positions of the chosen candidate papers, MOST RELEVANT FIRST. "
            "Empty list if none are relevant."
        )
    )
    reasoning: str = Field(description="One short paragraph explaining the picks and notable rejections.")


class Assessment(BaseModel):
    """Deep-read LLM output: one paper's judgment vs the gap.

    No paper attached — the deep_read step attaches the Paper to build a
    common.PaperAssessment. (Same "model doesn't echo ids" principle as Screening.)
    """

    relevance_to_gap: RelevanceToGap
    contribution_summary: str = Field(description="What the paper does that bears on the gap (2-4 sentences).")
    what_remains_open: str = Field(
        description="The part of the gap this paper does NOT address. If it fully solves the gap, say so."
    )
    evidence_snippets: List[str] = Field(
        default_factory=list,
        description="1-3 short VERBATIM quotes from the paper supporting the judgment; empty if not_addressed.",
    )


class VerdictJudgment(BaseModel):
    """Verdict LLM output: the synthesized judgment on the gap.

    No gap_id / assessments / metadata / refined_gap — the verdict step attaches
    those to build a common.Verdict. (Same "model doesn't echo ids" principle.)
    """

    status: VerdictStatus
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description=(
            "How sure you are in this verdict, from evidence strength and how directly "
            "the papers hit the gap. Low when the read is thin or papers only graze the gap."
        ),
    )
    reasoning: str = Field(
        description="Short synthesis across the assessments justifying the status and confidence."
    )
