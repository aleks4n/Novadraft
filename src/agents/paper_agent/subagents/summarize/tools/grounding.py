"""Deterministic grounding check for the summarize subagent's critic.

No LLM. Pulls every quoted value out of a produced ``summary`` (metric values,
dataset names, numbers in the results headline) and substring-matches them against
the paper's extracted ``content.sections``. Anything that does not show up verbatim
(modulo whitespace/case normalization) is flagged as a ``major`` issue upstream.

Substring matching over normalized text, no fuzzy matching: false negatives fall
through to the LLM critic rather than passing silently.
"""
from __future__ import annotations

import re

_NUMERIC_TOKEN = re.compile(r"\d+\.\d+%?|\d{2,}%?")


def _normalize(text: str) -> str:
    return " ".join(text.lower().split())


def _numbers_in(text: str) -> list[str]:
    """Numeric tokens worth checking: decimals, or 2+ digit ints, optional %."""
    return _NUMERIC_TOKEN.findall(text)


def check_grounding(summary: dict, sections: dict[str, str]) -> dict:
    """Returns {"checked": [...], "unmatched": [...]} (string values only)."""
    haystack = _normalize(" ".join(sections.values()))

    candidates: list[str] = []
    for metric in summary.get("metrics") or []:
        value = metric.get("value")
        if value:
            candidates.append(str(value))
        dataset = metric.get("dataset")
        if dataset:
            candidates.append(str(dataset))
    for dataset in summary.get("datasets") or []:
        name = dataset.get("name")
        if name:
            candidates.append(str(name))
    headline = (summary.get("results") or {}).get("headline")
    if headline:
        candidates.extend(_numbers_in(headline))

    checked: list[str] = []
    unmatched: list[str] = []
    seen: set[str] = set()
    for raw in candidates:
        norm = _normalize(raw)
        if not norm or norm in seen:
            continue
        seen.add(norm)
        checked.append(raw)
        # Tolerate a trailing "%" mismatch between summary and source text.
        found = norm in haystack or norm.rstrip("%") in haystack
        if not found:
            unmatched.append(raw)

    return {"checked": checked, "unmatched": unmatched}
