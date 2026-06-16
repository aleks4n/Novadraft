"""Shared Gap schema (INTERIM — mirrors survey_gap's gap.json).

Owned by the survey_gap agent; reproduced here so the validator compiles and is
testable. Two things the validator needs that gap.json doesn't yet carry, both
flagged for survey_gap:
  * `Evidence.published_date` — each evidence paper's date; the validator takes
    the EARLIEST as the prior-art cutoff (arXiv search looks strictly after it).
  * `Gap.source_keywords` — the source paper's keywords/topics, used by query-gen
    to anchor every search query to the right field.
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class Evidence(BaseModel):
    """One grounding for the gap — a quote from a paper. A gap may have several
    (e.g. the director agent merges a duplicate gap and appends another paper's
    evidence), so this is a list on Gap and each item carries its own paper_id."""

    paper_id: str
    section: str = ""
    quote: str = ""
    char_offset: Optional[List[int]] = None
    published_date: Optional[str] = None  # YYYY-MM-DD; the evidence paper's date


class Gap(BaseModel):
    gap_id: str
    title: str
    description: str
    canonical_description: str = ""
    type: str = ""  # e.g. "future_work", "limitation"
    status: str = "unchecked"
    evidence: List[Evidence] = Field(default_factory=list)
    source_keywords: List[str] = Field(default_factory=list)
    extracted_at: str = ""
