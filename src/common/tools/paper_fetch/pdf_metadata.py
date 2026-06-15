"""Fallback metadata extraction from a downloaded PDF's /Info dictionary.

Used when a fetcher couldn't fill in title/authors/year from an API (e.g. a
bare PDF URL with no Crossref/arXiv record). Best-effort and cheap — reads
only the PDF's document-info dictionary, no full parse/OCR.
"""
from __future__ import annotations

import re
from pathlib import Path

_AUTHOR_SPLIT_RE = re.compile(r"\s*(?:,|;|\band\b|&)\s*")
_YEAR_RE = re.compile(r"(\d{4})")


def _as_text(value) -> str | None:
    from pdfminer.psparser import PSLiteral
    from pdfminer.utils import decode_text

    if isinstance(value, bytes):
        text = decode_text(value).strip()
        return text or None
    if isinstance(value, PSLiteral):
        return value.name or None
    if isinstance(value, str):
        return value.strip() or None
    return None


def extract_pdf_metadata(pdf_path: Path) -> dict:
    """Best-effort title/authors/year from a PDF's document-info dictionary.

    Returns a dict containing only the keys it could recover.

    Raises ImportError if pdfminer.six is not installed (caller treats this
    like any other extraction failure and records a warning).
    """
    from pdfminer.pdfdocument import PDFDocument
    from pdfminer.pdfparser import PDFParser

    with open(pdf_path, "rb") as f:
        doc = PDFDocument(PDFParser(f))
        info = doc.info[0] if doc.info else {}

    out: dict = {}

    title = _as_text(info.get("Title"))
    if title:
        out["title"] = title

    author = _as_text(info.get("Author"))
    if author:
        authors = [a for a in _AUTHOR_SPLIT_RE.split(author) if a]
        if authors:
            out["authors"] = authors

    for key in ("CreationDate", "ModDate"):
        date = _as_text(info.get(key))
        if date:
            m = _YEAR_RE.search(date)
            if m:
                out["year"] = int(m.group(1))
                break

    return out
