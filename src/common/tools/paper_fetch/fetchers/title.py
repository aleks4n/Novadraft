"""Title search via Semantic Scholar, OpenAlex, and arXiv.

Strategy:
    1. Query Semantic Scholar, OpenAlex, AND arXiv title search; merge up to
       MAX_CANDIDATES candidates. arXiv is the most reliable of the three
       (no auth, generous limits) and acts as a fallback when the other two
       are rate-limited or down.
    2. If the top candidate's normalized title matches the query, fetch it.
    3. Otherwise return a list of candidates for the user to pick from.
    4. If no candidates anywhere, raise PaperFetchError.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from urllib.parse import quote

from ..date_util import iso_from_text, year_of
from ..errors import PaperFetchError
from ..http_util import get_json, get_text

_S2_API = "https://api.semanticscholar.org/graph/v1/paper/search"
_S2_FIELDS = "title,authors,year,publicationDate,venue,externalIds,openAccessPdf"
_OPENALEX_API = "https://api.openalex.org/works"
_ARXIV_SEARCH_API = "https://export.arxiv.org/api/query"
_ATOM_NS = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}

MAX_CANDIDATES = 5
# Per-request timeout for search lookups. OpenAlex in particular can take
# 15-30s under load, so this is generous enough to ride out slow responses
# while still letting retries in http_util kick in for genuine hangs.
_SEARCH_TIMEOUT = 25

# Typographic punctuation -> ASCII. Search backends (especially arXiv's
# quoted ti: search) treat these as different characters from their ASCII
# equivalents and can return zero results for an otherwise-exact title.
_PUNCT_MAP = {
    "‘": "'", "’": "'",  # ‘ ’
    "“": '"', "”": '"',  # “ ”
    "–": "-", "—": "-",  # – —
    "…": "...",  # …
}
_PUNCT_RE = re.compile("|".join(re.escape(c) for c in _PUNCT_MAP))


def _normalize_query(title: str) -> str:
    return _PUNCT_RE.sub(lambda m: _PUNCT_MAP[m.group(0)], title or "")


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def _s2_candidates(title: str) -> list[dict]:
    title = _normalize_query(title)
    url = (
        f"{_S2_API}?query={quote(title)}&limit={MAX_CANDIDATES}"
        f"&fields={_S2_FIELDS}"
    )
    data = get_json(url, timeout=_SEARCH_TIMEOUT)
    out = []
    for hit in data.get("data") or []:
        ext = hit.get("externalIds") or {}
        # publicationDate is "YYYY-MM-DD" when known, else null -> fall back to year.
        pub_date = iso_from_text(hit.get("publicationDate")) or year_of(hit.get("year"))
        out.append({
            "title": hit.get("title"),
            "authors": [a.get("name", "") for a in (hit.get("authors") or [])],
            "publication_date": pub_date and str(pub_date),
            "year": hit.get("year") or year_of(pub_date),
            "venue": hit.get("venue"),
            "arxiv_id": ext.get("ArXiv"),
            "doi": ext.get("DOI"),
            "pdf_url": (hit.get("openAccessPdf") or {}).get("url"),
            "semantic_scholar_id": hit.get("paperId"),
            "source": "semantic_scholar",
        })
    return out


_ARXIV_URL_RE = re.compile(
    r"arxiv\.org/(?:abs|pdf)/([a-z\-]+(?:\.[A-Z]{2})?/\d{7}|\d{4}\.\d{4,5})(?:v\d+)?",
    re.IGNORECASE,
)


def _arxiv_id_from_locations(hit: dict) -> str | None:
    """OpenAlex no longer exposes ids.arxiv; dig the arXiv id out of locations."""
    locations = [hit.get("primary_location")] + (hit.get("locations") or [])
    for loc in locations:
        if not loc:
            continue
        for url in (loc.get("landing_page_url"), loc.get("pdf_url")):
            if not url:
                continue
            m = _ARXIV_URL_RE.search(url)
            if m:
                return m.group(1)
    return None


def _openalex_candidates(title: str) -> list[dict]:
    # OpenAlex treats "?" and "*" in `search` as wildcards and rejects queries
    # where literal text precedes them (common in paper titles like "... ?").
    query = re.sub(r"[?*]", " ", _normalize_query(title)).strip()
    url = f"{_OPENALEX_API}?search={quote(query)}&per_page={MAX_CANDIDATES}"
    data = get_json(url, timeout=_SEARCH_TIMEOUT)
    out = []
    for hit in data.get("results") or []:
        authors = [(a.get("author") or {}).get("display_name", "") for a in (hit.get("authorships") or [])]
        ext_ids = hit.get("ids") or {}
        doi = None
        if doi_str := ext_ids.get("doi"):
            doi = doi_str.replace("https://doi.org/", "")
        venue = ((hit.get("primary_location") or {}).get("source") or {}).get("display_name")
        # OpenAlex returns publication_date ("YYYY-MM-DD") by default.
        pub_date = iso_from_text(hit.get("publication_date")) or year_of(hit.get("publication_year"))
        out.append({
            "title": hit.get("title") or "",
            "authors": authors,
            "publication_date": pub_date and str(pub_date),
            "year": hit.get("publication_year") or year_of(pub_date),
            "venue": venue,
            "arxiv_id": _arxiv_id_from_locations(hit),
            "doi": doi,
            "pdf_url": (hit.get("open_access") or {}).get("oa_url"),
            "semantic_scholar_id": None,
            "source": "openalex",
        })
    return out


def _arxiv_candidates(title: str) -> list[dict]:
    """Search arXiv's own export API by title. No auth, generous limits —
    used as a fallback when Semantic Scholar / OpenAlex are unavailable.
    """
    query = _normalize_query(title).replace('"', " ").strip()
    search_query = quote(f'ti:"{query}"')
    url = f"{_ARXIV_SEARCH_API}?search_query={search_query}&max_results={MAX_CANDIDATES}"
    xml_text = get_text(url, timeout=_SEARCH_TIMEOUT)
    root = ET.fromstring(xml_text)
    out = []
    for entry in root.findall("a:entry", _ATOM_NS):
        entry_title = re.sub(
            r"\s+", " ", entry.findtext("a:title", default="", namespaces=_ATOM_NS)
        ).strip()
        authors = [
            a.findtext("a:name", default="", namespaces=_ATOM_NS).strip()
            for a in entry.findall("a:author", _ATOM_NS)
        ]
        entry_id = entry.findtext("a:id", default="", namespaces=_ATOM_NS)
        m = re.search(r"abs/([a-z\-]+(?:\.[A-Z]{2})?/\d{7}|\d{4}\.\d{4,5})(?:v\d+)?", entry_id)
        arxiv_id = m.group(1) if m else None
        published = entry.findtext("a:published", default="", namespaces=_ATOM_NS)
        pub_date = iso_from_text(published)
        out.append({
            "title": entry_title,
            "authors": authors,
            "publication_date": pub_date,
            "year": year_of(pub_date),
            "venue": entry.findtext("arxiv:journal_ref", default=None, namespaces=_ATOM_NS),
            "arxiv_id": arxiv_id,
            "doi": entry.findtext("arxiv:doi", default=None, namespaces=_ATOM_NS),
            "pdf_url": f"https://arxiv.org/pdf/{arxiv_id}" if arxiv_id else None,
            "semantic_scholar_id": None,
            "source": "arxiv",
        })
    return out


def _dedup_merge(*lists: list[dict]) -> list[dict]:
    """Merge candidate lists, dedup by arxiv_id/doi/normalized title.

    When a duplicate is found across backends, the kept candidate is
    enriched with any arxiv_id/doi/pdf_url the duplicate has but it
    doesn't — so e.g. an OpenAlex hit can supply the arXiv id for a
    candidate that Semantic Scholar returned without one.
    """
    seen: dict[str, dict] = {}
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
            existing = next((seen[k] for k in keys if k in seen), None)
            if existing is not None:
                for field in ("arxiv_id", "doi", "pdf_url"):
                    if not existing.get(field) and c.get(field):
                        existing[field] = c[field]
                # Prefer the most precise date across backends (e.g. a full
                # "2023-04-15" from OpenAlex over a year-only "2023" from S2).
                if len(str(c.get("publication_date") or "")) > len(str(existing.get("publication_date") or "")):
                    existing["publication_date"] = c["publication_date"]
                    existing["year"] = c.get("year") or existing.get("year")
                for k in keys:
                    seen[k] = existing
                continue
            seen.update({k: c for k in keys})
            out.append(c)
            if len(out) >= MAX_CANDIDATES:
                return out
    return out


def search_candidates(title: str) -> tuple[list[dict], list[tuple[str, str]]]:
    """Return (candidates, attempts) — attempts records which backends failed."""
    attempts: list[tuple[str, str]] = []
    s2: list[dict] = []
    oa: list[dict] = []
    ax: list[dict] = []
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
    try:
        ax = _arxiv_candidates(title)
        if not ax:
            attempts.append(("arxiv", "no_results"))
    except Exception as e:
        attempts.append(("arxiv", repr(e)))
    return _dedup_merge(s2, oa, ax), attempts


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
