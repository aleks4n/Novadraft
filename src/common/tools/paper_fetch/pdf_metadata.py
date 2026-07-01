"""Metadata and identifier extraction from a downloaded PDF's /Info dictionary.

Best-effort and cheap — reads only the PDF's document-info dictionary, no
full parse/OCR.
"""
from __future__ import annotations

import re
from pathlib import Path

from .date_util import iso_from_pdf_date, year_of

_AUTHOR_SPLIT_RE = re.compile(r"\s*(?:,|;|\band\b|&)\s*")
_ARXIV_ID_RE = re.compile(
    r"arXiv[:\s]+(\d{4}\.\d{4,5}(?:v\d+)?|[a-z\-]+(?:\.[A-Z]{2})?/\d{7}(?:v\d+)?)",
    re.IGNORECASE,
)
_DOI_RE = re.compile(r"\b(10\.\d{4,9}/\S+)")


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
            iso = iso_from_pdf_date(date)
            if iso:
                out["publication_date"] = iso
                out["year"] = year_of(iso)
                break

    return out


def extract_pdf_identifiers(pdf_path: Path) -> dict:
    """Extract arXiv ID and/or DOI from a PDF's Info dictionary fields.

    Checks Subject, Keywords, Creator, Producer, and Title fields (in that
    order). Returns a dict containing any of: {"arxiv_id": ..., "doi": ...}.
    """
    from pdfminer.pdfdocument import PDFDocument
    from pdfminer.pdfparser import PDFParser

    with open(pdf_path, "rb") as f:
        doc = PDFDocument(PDFParser(f))
        info = doc.info[0] if doc.info else {}

    out: dict = {}
    for field in ("Subject", "Keywords", "Creator", "Producer", "Title"):
        text = _as_text(info.get(field)) or ""
        if not text:
            continue
        if "arxiv_id" not in out:
            m = _ARXIV_ID_RE.search(text)
            if m:
                out["arxiv_id"] = re.sub(r"v\d+$", "", m.group(1))
        if "doi" not in out:
            m = _DOI_RE.search(text)
            if m:
                out["doi"] = m.group(1).rstrip(".,)")
    return out
