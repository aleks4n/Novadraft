"""Pydantic schema for paper.json — the central storage record for a paper."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class PaperIdentifiers(BaseModel):
    arxiv_id: Optional[str] = None
    doi: Optional[str] = None
    semantic_scholar_id: Optional[str] = None
    openreview_id: Optional[str] = None


class PaperMetadata(BaseModel):
    title: Optional[str] = None
    authors: list[str] = Field(default_factory=list)
    year: Optional[int] = None
    # Most precise ISO-8601 prefix available: "YYYY", "YYYY-MM", or
    # "YYYY-MM-DD". Components are never padded — a year-only source stays
    # "2023". `year` is the integer view of this. See paper_fetch/date_util.py.
    publication_date: Optional[str] = None
    venue: Optional[str] = None
    # --- venue/citation enrichment (paper_fetch/enrich.py; Semantic Scholar, OpenAlex
    # fallback). arXiv only reports a venue in `journal_ref`, which authors add by hand
    # after journal publication and almost never set, so these stay None until enriched.
    venue_type: Optional[str] = None  # "conference" | "journal" | "workshop" | ...
    # Venue acronyms as the provider gives them ("ICLR", "NeurIPS"). Preferred over the
    # LLM name->acronym resolver in ranking_agent.venue, which only sees the messy string.
    venue_acronyms: list[str] = Field(default_factory=list)
    citation_count: Optional[int] = None
    influential_citation_count: Optional[int] = None
    # When the counts were read — they grow over time, so a stale record is still usable
    # but knowable as stale.
    enriched_at: Optional[str] = None
    enriched_via: Optional[str] = None  # "semantic_scholar" | "openalex"
    abstract: Optional[str] = None
    identifiers: PaperIdentifiers = Field(default_factory=PaperIdentifiers)


class PaperFiles(BaseModel):
    pdf: Optional[str] = None
    tex_dir: Optional[str] = None
    tex_main: Optional[str] = None


class PaperSource(BaseModel):
    input_query: str
    input_type: str
    resolved_via: Optional[str] = None
    source_url: Optional[str] = None
    files: PaperFiles = Field(default_factory=PaperFiles)
    fetched_at: str


class PaperProcessing(BaseModel):
    fetched: bool = True
    extracted: bool = False
    summarized: bool = False
    summary_verified: bool = False
    gaps_extracted: bool = False


class PaperRecord(BaseModel):
    paper_id: str
    metadata: PaperMetadata = Field(default_factory=PaperMetadata)
    source: Optional[PaperSource] = None
    content: Optional[dict] = None
    summary: Optional[dict] = None
    summary_verification: Optional[dict] = None
    gaps: dict = Field(default_factory=lambda: {"gap_ids": []})
    processing: PaperProcessing = Field(default_factory=PaperProcessing)
    warnings: list[str] = Field(default_factory=list)


class CandidatePaper(BaseModel):
    """A lightweight view of an *external* paper found during validation."""

    paper_id: str  # e.g. arXiv id
    title: str
    abstract: str = ""
    authors: list[str] = Field(default_factory=list)
    published_date: str = ""
    url: str = ""
