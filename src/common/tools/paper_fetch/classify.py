"""
Deterministic router that classifies a user-supplied query string.
Order matters: first match wins.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlparse

InputType = Literal["arxiv_id", "arxiv_url", "doi", "doi_url", "pdf_url", "landing_url", "url", "title"]


@dataclass
class Classified:
    input_type: InputType
    value: str           # canonical extracted value (e.g. bare arxiv id, bare doi, url, or title)
    original: str


_ARXIV_NEW = re.compile(r"^(arxiv:)?(\d{4}\.\d{4,5})(v\d+)?$", re.IGNORECASE)
_ARXIV_OLD = re.compile(r"^([a-z\-]+(\.[A-Z]{2})?/\d{7})(v\d+)?$")
_DOI = re.compile(r"^10\.\d{4,9}/\S+$")
_ARXIV_EMBEDDED = re.compile(
    r"(?:\barxiv\s*:\s*|arxiv\.org/(?:abs|pdf)/)"
    r"([a-z\-]+(?:\.[A-Z]{2})?/\d{7}|\d{4}\.\d{4,5})(?:v\d+)?",
    re.IGNORECASE,
)
_DOI_EMBEDDED = re.compile(r"(?:\bdoi\s*:\s*|doi\.org/)(10\.\d{4,9}/\S+)", re.IGNORECASE)
_LANDING_HOSTS = {
    "arxiv.org", "www.arxiv.org",
    "openreview.net",
    "aclanthology.org", "www.aclanthology.org",
    "semanticscholar.org", "www.semanticscholar.org",
    "proceedings.mlr.press",
    "papers.nips.cc", "papers.neurips.cc", "proceedings.neurips.cc",
    "dl.acm.org",
}


def classify(query: str) -> Classified:
    q = query.strip()

    # arXiv ID (bare)
    m = _ARXIV_NEW.match(q)
    if m:
        return Classified("arxiv_id", m.group(2), q)
    m = _ARXIV_OLD.match(q)
    if m:
        return Classified("arxiv_id", m.group(1), q)

    # DOI (bare)
    if _DOI.match(q):
        return Classified("doi", q, q)

    # URL?
    if q.startswith(("http://", "https://")):
        parsed = urlparse(q)
        host = parsed.netloc.lower()
        path = parsed.path

        # arXiv URL
        if host.endswith("arxiv.org"):
            # /abs/<id>, /pdf/<id>, /pdf/<id>.pdf
            m = re.search(r"/(?:abs|pdf)/([a-z\-]+(?:\.[A-Z]{2})?/\d{7}|\d{4}\.\d{4,5})(v\d+)?(?:\.pdf)?", path)
            if m:
                return Classified("arxiv_url", m.group(1), q)

        # doi.org URL
        if host in ("doi.org", "dx.doi.org", "www.doi.org"):
            doi = path.lstrip("/")
            if _DOI.match(doi):
                return Classified("doi_url", doi, q)

        # Direct PDF
        if path.lower().endswith(".pdf"):
            return Classified("pdf_url", q, q)

        # Known landing page
        if host in _LANDING_HOSTS:
            return Classified("landing_url", q, q)

        return Classified("url", q, q)

    # A caller may include a helpful title beside an exact identifier. The identifier is
    # still the fastest and least ambiguous route, so do not fall through to title search.
    if match := _ARXIV_EMBEDDED.search(q):
        return Classified("arxiv_id", match.group(1), q)
    if match := _DOI_EMBEDDED.search(q):
        return Classified("doi", match.group(1).rstrip(".,);"), q)

    # Fall through: treat as title
    return Classified("title", q, q)
