"""Title search via Semantic Scholar and OpenAlex.

Strategy:
    1. Query Semantic Scholar AND OpenAlex title search; merge up to 5 candidates.
    2. If the top candidate's normalized title matches the query, fetch it.
    3. Otherwise return a list of candidates for the user to pick from.
    4. If no candidates anywhere, raise PaperFetchError.

OpenAlex is used instead of arXiv for title search because:
    - Better relevance ranking (ML-based, not simple keyword matching)
    - Handles spelling mistakes and typos (e.g. "Probab" → "Probabilistic")
    - Free, no auth required, generous rate limits (100k/day)
    - Returns both arXiv IDs and DOIs for rich paper identification
"""
from __future__ import annotations

import re
from urllib.parse import quote

from ..errors import PaperFetchError
from ..http_util import get_json

_S2_API = "https://api.semanticscholar.org/graph/v1/paper/search"
_S2_FIELDS = "title,authors,year,venue,externalIds"
_OPENALEX_API = "https://api.openalex.org/works"

MAX_CANDIDATES = 5


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def _s2_candidates(title: str) -> list[dict]:
    url = (
        f"{_S2_API}?query={quote(title)}&limit={MAX_CANDIDATES}"
        f"&fields={_S2_FIELDS}"
    )
    data = get_json(url)
    out = []
    for hit in data.get("data") or []:
        ext = hit.get("externalIds") or {}
        out.append({
            "title": hit.get("title"),
            "authors": [a.get("name", "") for a in (hit.get("authors") or [])],
            "year": hit.get("year"),
            "venue": hit.get("venue"),
            "arxiv_id": ext.get("ArXiv"),
            "doi": ext.get("DOI"),
            "semantic_scholar_id": hit.get("paperId"),
            "source": "semantic_scholar",
        })
    return out


def _openalex_candidates(title: str) -> list[dict]:
    """Search OpenAlex. Better relevance ranking than arXiv, handles typos well."""
    url = f"{_OPENALEX_API}?search={quote(title)}&per_page={MAX_CANDIDATES}"
    data = get_json(url)
    out = []
    for hit in data.get("results") or []:
        authors = [a.get("display_name", "") for a in (hit.get("authorships") or [])]
        ext_ids = hit.get("ids") or {}
        arxiv_id = None
        doi = None
        if arxiv := ext_ids.get("arxiv"):
            arxiv_id = arxiv.replace("https://arxiv.org/abs/", "")
        if doi_str := ext_ids.get("doi"):
            doi = doi_str.replace("https://doi.org/", "")
        out.append({
            "title": hit.get("title") or "",
            "authors": authors,
            "year": hit.get("publication_year"),
            "venue": (hit.get("host_venue") or {}).get("display_name"),
            "arxiv_id": arxiv_id,
            "doi": doi,
            "semantic_scholar_id": None,
            "source": "openalex",
        })
    return out


def _dedup_merge(*lists: list[dict]) -> list[dict]:
    """Merge candidate lists, dedup by arxiv_id/doi/normalized title."""
    seen: set[str] = set()
    out: list[dict] = []
    for lst in lists:
        for c in lst:
            keys = []
            if c.get("arxiv_id"):
                keys.append(f"arxiv:{c['arxiv_id']}")
            if c.get("doi"):
                keys.append(f"doi:{c['doi'].lower()}")
            if c.get("title"):
                keys.append(f"title:{_norm(c['title'])}")
            if any(k in seen for k in keys):
                continue
            seen.update(keys)
            out.append(c)
            if len(out) >= MAX_CANDIDATES:
                return out
    return out


def search_candidates(title: str) -> tuple[list[dict], list[tuple[str, str]]]:
    """Return (candidates, attempts) — attempts records which backends failed."""
    attempts: list[tuple[str, str]] = []
    s2: list[dict] = []
    oa: list[dict] = []
    try:
        s2 = _s2_candidates(title)
        if not s2:
            attempts.append(("semantic_scholar", "no_results"))
    except Exception as e:
        attempts.append(("semantic_scholar", repr(e)))
    try:
        oa = _openalex_candidates(title)
        if not oa:
            attempts.append(("openalex", "no_results"))
    except Exception as e:
        attempts.append(("openalex", repr(e)))
    return _dedup_merge(s2, oa), attempts


def resolve_title(title: str, query: str) -> tuple[dict | None, list[dict]]:
    """Return (chosen_candidate_or_None, all_candidates).

    chosen is set only when the top candidate's title matches the query.
    Raises PaperFetchError if nothing was found.
    """
    candidates, attempts = search_candidates(title)
    if not candidates:
        raise PaperFetchError(query, attempts)
    top = candidates[0]
    if _norm(top.get("title") or "") == _norm(title):
        return top, candidates
    return None, candidates
