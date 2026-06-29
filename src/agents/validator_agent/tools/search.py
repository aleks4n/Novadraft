from __future__ import annotations

import re
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import List

import feedparser

from src.common.schemas.gap import GapRecord
from src.common.schemas.paper import CandidatePaper
from src.common.tools.paper_access import get_year
from src.agents.validator_agent.schemas import SearchQuery
from src.agents.validator_agent.state import ValidatorState

_ARXIV_API = "https://export.arxiv.org/api/query?"
_MAX_RESULTS = 50
_QUERY_SPACING = 3.0  # seconds between queries — arXiv rate-limits

_RETRYABLE_STATUS = {429, 500, 502, 503, 504} # permanent error (e.g. 400 bad query) is not retried.
_RETRIES = 4
_BACKOFF_BASE = 4.0  # 4s, 8s, 16s, 32s

_SURVEY_RE = re.compile(r"(?i)\b(survey|review|overview)\b")
_WS_RE = re.compile(r"\s+")


# --- query construction ---

def _term(t: str) -> str:
    """Wraps a term in quotes if it contains whitespace, for arXiv's `all:` search field."""
    return f'all:"{t}"' if " " in t else f"all:{t}"


def _build_clause(concepts: List[List[str]]) -> str:
    """
    Builds a boolean query clause from the model's concept groups.
    Each inner list is one concept's synonyms; OR within a group, AND across groups.
    """
    parts = []
    for group in concepts:
        terms = [_term(t) for t in group]
        if not terms:
            continue
        parts.append(f"({' OR '.join(terms)})" if len(terms) > 1 else terms[0])
    return " AND ".join(parts)


def _date_clause(cutoff_date: str) -> str:
    """Parses cutoff date to arXiv's `submittedDate:[YYYYMMDDHHMM TO YYYYMMDDHHMM]` format."""
    start = (datetime.strptime(cutoff_date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y%m%d%H%M")
    end = datetime.now(timezone.utc).strftime("%Y%m%d%H%M")
    return f"submittedDate:[{start} TO {end}]"


def _cutoff_date(gap: GapRecord, category_slug: str) -> str:
    """Earliest source-paper year -> a `YYYY-01-01` prior-art cutoff (search looks after it).

    paper.json carries only the year, which is enough here: we want work published
    from the source paper's year onward. Source papers are the gap's evidence papers.
    """
    years = [y for ev in gap.evidence if (y := get_year(category_slug, ev.paper_id)) is not None]
    if not years:
        raise ValueError(
            f"gap {gap.gap_id}: no source-paper year available (paper.json not fetched?) "
            "— cannot set a prior-art cutoff."
        )
    return f"{min(years)}-01-01"


# --- arXiv ---

def _collapse(text: str) -> str:
    return _WS_RE.sub(" ", text).strip()


def _bare_id(raw: str) -> str:
    """Extract a bare arXiv id (strip URL prefix + version) from an entry id/link."""
    m = re.search(r"(\d{4}\.\d{4,5})(v\d+)?", raw)
    return m.group(1) if m else raw


def _parse_entry(entry) -> CandidatePaper:
    """Parse a feedparser entry from arXiv's API into a CandidatePaper."""
    return CandidatePaper(
        paper_id=f"arxiv_{_bare_id(entry.get('id', '') or entry.get('link', ''))}",
        title=_collapse(entry.get("title", "")),
        abstract=_collapse(entry.get("summary", "")),
        authors=[a.name for a in entry.get("authors", [])],
        published_date=entry.get("published", ""),
        url=entry.get("link", ""),
    )


def _search_one(query: SearchQuery, cutoff_date: str) -> List[CandidatePaper]:
    """
    Run one search query against arXiv and return the results as Papers (drop surveys). 
    Raises if arXiv returns an error status.
    """
    search_query = f"{_build_clause(query.concepts)} AND {_date_clause(cutoff_date)}"
    url = _ARXIV_API + urllib.parse.urlencode(
        {
            "search_query": search_query,
            "start": 0,
            "max_results": _MAX_RESULTS,
            "sortBy": "relevance",
            "sortOrder": "descending",
        }
    )

    for attempt in range(_RETRIES + 1):
        feed = feedparser.parse(url)
        status = feed.get("status")
        if status is None or status == 200:
            break
        if status in _RETRYABLE_STATUS and attempt < _RETRIES:
            time.sleep(_BACKOFF_BASE * (2**attempt))
            continue
        raise RuntimeError(f"arXiv returned HTTP {status} for {query.query_type} query")

    papers = [_parse_entry(e) for e in feed.entries]
    return [p for p in papers if not _SURVEY_RE.search(p.title)]


# --- search ---

def search(state: ValidatorState) -> dict:
    """LangGraph node: run the 3 queries -> {"search_results": [ranked list per query]}."""
    gap: GapRecord = state["gap"]
    cutoff = _cutoff_date(gap, state["category_slug"])

    results: List[List[CandidatePaper]] = []
    for i, query in enumerate(state["search_queries"].queries):
        results.append(_search_one(query, cutoff))
        if i < len(state["search_queries"].queries) - 1:
            time.sleep(_QUERY_SPACING)  # be polite to arXiv between queries
    return {"search_results": results}
