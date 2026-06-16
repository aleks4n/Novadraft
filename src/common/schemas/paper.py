"""Shared Paper schema (INTERIM).

The validator only consumes a *view* of a paper. The authoritative, richer schema
is owned by the survey_gap agent (its paper.json: metadata / source / content /
summary). This interim type holds just the fields the validator actually uses, so
the pipeline compiles and is testable — reconcile with that agent before locking.
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class Paper(BaseModel):
    paper_id: str  # team convention, e.g. "arxiv_1406.2661" (a bare arXiv id also works)
    title: str
    abstract: str = ""
    authors: List[str] = Field(default_factory=list)
    published_date: str = ""  # ISO date or year; from arXiv search for search candidates
    url: str = ""
    # Full text for Deep Read. Populated by the common download/extract helper
    # (survey_gap's paper.json already carries this as content.full_text).
    full_text: Optional[str] = None
