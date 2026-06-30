"""Normalize publication dates to partial-ISO strings.

A publication date is stored as the most precise ISO-8601 prefix we can
recover from the source: ``"YYYY"``, ``"YYYY-MM"``, or ``"YYYY-MM-DD"``.
We never pad missing components (a year-only source stays ``"2023"``, not
``"2023-01-01"``) so the stored value is honest about its precision.
``year`` is always derivable from it via :func:`year_of`.
"""
from __future__ import annotations

import re

# Leading ISO date/datetime, e.g. "2023" / "2023-04" / "2023-04-15T12:34:56Z".
_ISO_PREFIX_RE = re.compile(r"(\d{4})(?:-(\d{1,2}))?(?:-(\d{1,2}))?")
# Compact PDF /Info date, e.g. "D:20230415123456+02'00'" or "20230415".
_PDF_COMPACT_RE = re.compile(r"(\d{4})(\d{2})?(\d{2})?")


def _assemble(year, month, day) -> str | None:
    if not year:
        return None
    out = f"{int(year):04d}"
    if month and int(month):
        out += f"-{int(month):02d}"
        if day and int(day):
            out += f"-{int(day):02d}"
    return out


def iso_from_text(text: str | None) -> str | None:
    """Parse an ISO-8601 date/datetime prefix into a partial-ISO date.

    e.g. arXiv's ``"2023-04-15T12:34:56Z"`` -> ``"2023-04-15"``. Returns
    None if no leading 4-digit year is present.
    """
    if not text:
        return None
    m = _ISO_PREFIX_RE.match(text.strip())
    return _assemble(*m.groups()) if m else None


def iso_from_pdf_date(text: str | None) -> str | None:
    """Parse a PDF /Info date (``"D:YYYYMMDD..."``) or an ISO string."""
    if not text:
        return None
    if "-" in text:
        return iso_from_text(text)
    m = _PDF_COMPACT_RE.search(text)
    return _assemble(*m.groups()) if m else None


def iso_from_date_parts(parts) -> str | None:
    """Parse Crossref ``date-parts`` (``[2023, 4, 15]``) into partial-ISO."""
    if not parts:
        return None
    nums = [p for p in parts if isinstance(p, int)]
    return _assemble(*(nums + [None, None, None])[:3]) if nums else None


def year_of(date_str: str | None) -> int | None:
    """Extract the year integer from a partial-ISO date string."""
    if not date_str:
        return None
    m = re.match(r"(\d{4})", str(date_str))
    return int(m.group(1)) if m else None
