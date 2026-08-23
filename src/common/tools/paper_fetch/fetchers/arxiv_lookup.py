"""Helpers for upgrading DOI / title → arXiv ID.

Priority order:
  - arxiv_id_for_title: arxiv search API first (generous limits, authoritative),
    Semantic Scholar as fallback.
  - arxiv_id_for_doi:   Semantic Scholar direct lookup (targeted, rarely rate-limited).
"""
from __future__ import annotations

import difflib
import re
import xml.etree.ElementTree as ET
from urllib.parse import quote

from ..http_util import get_json, get_text

_S2_BASE = "https://api.semanticscholar.org/graph/v1/paper"
_ARXIV_ATOM_NS = {"a": "http://www.w3.org/2005/Atom"}
_TITLE_SIMILARITY_THRESHOLD = 0.85


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9\s]", "", text.lower())


def _similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, _normalize(a), _normalize(b)).ratio()


def arxiv_id_for_doi(doi: str) -> str | None:
    """Return the arXiv ID for a DOI via Semantic Scholar, or None on any failure."""
    try:
        data = get_json(f"{_S2_BASE}/DOI:{quote(doi, safe='/')}?fields=externalIds")
        return (data.get("externalIds") or {}).get("ArXiv")
    except Exception:
        return None


def _arxiv_search_by_title(title: str) -> str | None:
    """Search arxiv's own API by exact title phrase. Returns arxiv_id or None."""
    try:
        query = f'ti:"{title}"'
        url = f"https://export.arxiv.org/api/query?search_query={quote(query)}&max_results=3"
        xml_text = get_text(url)
        root = ET.fromstring(xml_text)
        for entry in root.findall("a:entry", _ARXIV_ATOM_NS):
            candidate = re.sub(
                r"\s+", " ",
                (entry.findtext("a:title", default="", namespaces=_ARXIV_ATOM_NS) or "").strip()
            )
            if _similarity(title, candidate) >= _TITLE_SIMILARITY_THRESHOLD:
                id_url = (entry.findtext("a:id", default="", namespaces=_ARXIV_ATOM_NS) or "").strip()
                m = re.search(r"/abs/(\d{4}\.\d{4,5}|[a-z\-]+/\d{7})(v\d+)?", id_url)
                if m:
                    return m.group(1)
    except Exception:
        pass
    return None


def arxiv_id_for_title(title: str) -> tuple[str | None, str | None]:
    """Search for a paper by title. Returns (arxiv_id, doi) for best confident match.

    Tries arxiv's own search API first (authoritative, no aggressive rate limits),
    then falls back to Semantic Scholar which also returns a DOI if no arxiv exists.

    Confidence gate: normalized title similarity >= 0.85 to avoid false substitutions.
    Returns (None, None) if no confident match is found.
    """
    # 1. Arxiv search — preferred: directly returns arxiv_id, no rate-limit issues
    arxiv_id = _arxiv_search_by_title(title)
    if arxiv_id:
        return arxiv_id, None

    # 2. Semantic Scholar fallback — can return a DOI even when no arxiv preprint exists
    try:
        data = get_json(
            f"{_S2_BASE}/search?query={quote(title)}&fields=externalIds,title&limit=3"
        )
    except Exception:
        return None, None

    for item in (data.get("data") or []):
        candidate = item.get("title") or ""
        if _similarity(title, candidate) >= _TITLE_SIMILARITY_THRESHOLD:
            ext = item.get("externalIds") or {}
            return ext.get("ArXiv"), ext.get("DOI")

    return None, None
