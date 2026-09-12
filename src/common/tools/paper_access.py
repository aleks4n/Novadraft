"""Typed read accessors over paper.json — the downstream (validator / ranking) read side.

`storage.read_paper_json` returns a raw dict, and `PaperRecord`'s nested blocks
(`content`, `summary`) are untyped dicts, so callers would otherwise index into
paper.json by hand. These getters centralise that: load + validate the record
once, then expose the handful of fields the downstream agents actually consume —
year + venue (metadata), keywords (summary), sections + full text (content).

All keyed by `(category_slug, paper_id)`: the workspace partitions papers by
category, so a `paper_id` alone can't locate the file. Every getter returns a
safe empty/None when the paper (or the block it reads) isn't there yet, so a
not-yet-fetched or not-yet-summarised paper degrades gracefully rather than raising.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from src.common.schemas.paper import PaperRecord
from src.common.utils import storage


def get_paper(category_slug: str, paper_id: str) -> Optional[PaperRecord]:
    """Load and validate a paper.json. None if it isn't in the workspace."""
    raw = storage.read_paper_json(category_slug, paper_id)
    if raw is None:
        return None
    return PaperRecord.model_validate(raw)


def get_year(category_slug: str, paper_id: str) -> Optional[int]:
    """Publication year (`metadata.year`); None if unknown."""
    paper = get_paper(category_slug, paper_id)
    return paper.metadata.year if paper else None


def get_venue(category_slug: str, paper_id: str) -> Optional[str]:
    """Venue name (`metadata.venue`); None if unknown. The ranking resolver maps
    this messy name to a CORE acronym downstream."""
    paper = get_paper(category_slug, paper_id)
    return paper.metadata.venue if paper else None


def get_venue_acronyms(category_slug: str, paper_id: str) -> List[str]:
    """Venue acronyms as the enrichment provider reported them (["ICLR"], ["NeurIPS"]);
    empty when the paper has not been enriched. Preferred over the ranking agent's LLM
    name->acronym resolver, which only ever sees the messy venue string."""
    paper = get_paper(category_slug, paper_id)
    return list(paper.metadata.venue_acronyms) if paper else []


def get_venue_type(category_slug: str, paper_id: str) -> Optional[str]:
    """Venue kind ("conference", "journal", "workshop"); None if unknown. Lets the
    ranking agent refuse to score a workshop with its host conference's rank."""
    paper = get_paper(category_slug, paper_id)
    return paper.metadata.venue_type if paper else None


def get_citation_count(category_slug: str, paper_id: str) -> Optional[int]:
    """Citation count from the last enrichment; None if never enriched."""
    paper = get_paper(category_slug, paper_id)
    return paper.metadata.citation_count if paper else None


def get_keywords(category_slug: str, paper_id: str) -> List[str]:
    """The source paper's anchor terms: `summary.keywords` followed by
    `summary.topics`, deduped in order. Empty when the paper has no summary yet.

    Read straight off the summary dict (not via PaperSummary validation) so a
    keyword read never fails on an otherwise-incomplete summary block.
    """
    paper = get_paper(category_slug, paper_id)
    if paper is None or not paper.summary:
        return []
    out: List[str] = []
    for term in [*paper.summary.get("keywords", []), *paper.summary.get("topics", [])]:
        if term and term not in out:
            out.append(term)
    return out


def get_sections(category_slug: str, paper_id: str) -> Dict[str, str]:
    """`content.sections` (section name -> text); empty if not extracted yet."""
    paper = get_paper(category_slug, paper_id)
    if paper is None or not paper.content:
        return {}
    return paper.content.get("sections", {}) or {}


def get_full_text(category_slug: str, paper_id: str) -> Optional[str]:
    """`content.full_text` (the whole extracted body); None if not extracted yet."""
    paper = get_paper(category_slug, paper_id)
    if paper is None or not paper.content:
        return None
    return paper.content.get("full_text")
