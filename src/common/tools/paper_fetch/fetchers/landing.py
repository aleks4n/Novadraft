"""Landing-page handler: pull a canonical PDF URL or arXiv/DOI out of HTML
via the citation_pdf_url / citation_doi / citation_arxiv_id meta tags that
most academic sites expose.
"""
from __future__ import annotations

import re

from ..http_util import get_text


def resolve_landing(url: str) -> dict:
    """Return {"arxiv_id"|"doi"|"pdf_url": ...} or {} if nothing found."""
    try:
        html = get_text(url)
    except Exception:
        return {}

    def meta(name: str) -> str | None:
        m = re.search(
            rf'<meta[^>]+name=["\']{name}["\'][^>]+content=["\']([^"\']+)["\']',
            html, re.IGNORECASE,
        )
        return m.group(1).strip() if m else None

    arxiv_id = meta("citation_arxiv_id")
    if arxiv_id:
        return {"arxiv_id": arxiv_id}
    doi = meta("citation_doi")
    if doi:
        return {"doi": doi}
    pdf_url = meta("citation_pdf_url")
    if pdf_url:
        return {"pdf_url": pdf_url}
    return {}
