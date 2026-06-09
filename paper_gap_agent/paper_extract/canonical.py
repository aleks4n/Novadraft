"""Canonical section name aliasing.

Flat map: a literal heading from a paper -> canonical key. Unmapped headings
keep their literal name (lowercased, underscored).
"""
from __future__ import annotations

import re

ALIASES = {
    "abstract": "abstract",
    "introduction": "introduction",
    "intro": "introduction",
    "background": "background",
    "preliminaries": "background",
    "related work": "related_work",
    "related works": "related_work",
    "prior work": "related_work",
    "method": "method",
    "methods": "method",
    "methodology": "method",
    "approach": "method",
    "model": "method",
    "model architecture": "method",
    "architecture": "method",
    "proposed method": "method",
    "experiments": "experiments",
    "experimental setup": "experiments",
    "experimental results": "results",
    "results": "results",
    "evaluation": "results",
    "discussion": "discussion",
    "analysis": "discussion",
    "limitations": "limitations",
    "limitation": "limitations",
    "conclusion": "conclusion",
    "conclusions": "conclusion",
    "concluding remarks": "conclusion",
    "future work": "future_work",
    "future directions": "future_work",
    "references": "references",
    "bibliography": "references",
    "acknowledgments": "acknowledgments",
    "acknowledgements": "acknowledgments",
}


def canonicalize(heading: str) -> str:
    """Map a raw heading to a canonical key, or fall back to a slugified form."""
    h = re.sub(r"^\s*\d+(\.\d+)*\.?\s*", "", heading).strip().lower()
    h = re.sub(r"\s+", " ", h)
    if h in ALIASES:
        return ALIASES[h]
    return re.sub(r"[^a-z0-9]+", "_", h).strip("_") or "section"
