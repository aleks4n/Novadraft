"""CORE conference-ranking lookup for venue scoring.

CORE publishes A*/A/B/C ranks in dated editions, and ranks drift between them — so we
match a paper to the edition closest to its publication year. Editions live in
data/core/<EDITION>.csv (exported from portal.core.edu.au). Venue names are resolved to
CORE acronyms upstream by the LLM resolver (venue_resolver.py), so the lookup here is an
exact acronym -> rank -> [0,1] score; unknown -> None (impact then falls back to citation).
"""

from __future__ import annotations

import csv
import re
from functools import lru_cache
from pathlib import Path
from typing import Dict, Optional

_DATA_DIR = Path(__file__).parent / "data" / "core"
_TIER_SCORE = {"A*": 1.0, "A": 0.8, "B": 0.5, "C": 0.3}  # tunable rank -> score
_YEAR_RE = re.compile(r"(?:19|20)\d{2}")


def _normalize(name: str) -> str:
    """Lowercase, drop year tokens + punctuation, collapse whitespace."""
    name = name.lower()
    name = re.sub(r"\b(?:19|20)\d{2}\b", " ", name)  # strip "2022" year prefixes
    name = re.sub(r"[^a-z0-9 ]+", " ", name)
    return re.sub(r"\s+", " ", name).strip()


@lru_cache(maxsize=1)
def _editions() -> Dict[int, Dict[str, str]]:
    """{edition_year: {normalized title or acronym: rank}} — loaded once."""
    editions: Dict[int, Dict[str, str]] = {}
    for path in sorted(_DATA_DIR.glob("*.csv")):
        m = _YEAR_RE.search(path.stem)
        if not m:
            continue
        index: Dict[str, str] = {}
        for row in csv.reader(path.read_text(encoding="utf-8").splitlines()):
            if len(row) < 5 or row[4] not in _TIER_SCORE:
                continue
            title, acronym, rank = row[1], row[2], row[4]
            for key in (_normalize(title), _normalize(acronym)):
                if key:
                    index.setdefault(key, rank)
        editions[int(m.group())] = index
    return editions


def _closest_edition(year: Optional[int]) -> Optional[Dict[str, str]]:
    """The edition index nearest `year` (newest when year is unknown)."""
    eds = _editions()
    if not eds:
        return None
    target = max(eds) if year is None else min(eds, key=lambda y: abs(y - year))
    return eds[target]


def acronym_exists(acronym: str) -> bool:
    """True if the acronym appears in any CORE edition — used to ground the LLM resolver."""
    key = _normalize(acronym)
    return bool(key) and any(key in index for index in _editions().values())


def score_for_acronym(acronym: Optional[str], year: Optional[int]) -> Optional[float]:
    """[0,1] venue quality for a CORE acronym at the edition closest to `year`; None if unknown.

    Acronyms are resolved upstream by the LLM venue resolver, so the lookup here is exact.
    """
    if not acronym:
        return None
    index = _closest_edition(year)
    if not index:
        return None
    rank = index.get(_normalize(acronym))
    return _TIER_SCORE.get(rank) if rank else None
