"""LLM venue → CORE acronym resolver (batched, grounded, cached).

Venue names in paper.json are messy ("Advances in Neural Information Processing
Systems", "2022 IEEE/CVF Conference ... (CVPR)"); CORE keys on acronyms. The LLM
does the name→acronym normalization, then ``rankings`` looks the acronym up by
year. Each answer is grounded against the CORE list (acronyms not present are
dropped, so a hallucinated acronym cannot produce a rank) and name→acronym results
are cached to disk, so each unique venue is resolved once across runs.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from src.llm.registry import chat_with_structured_output, get_llm
from src.agents.ranking_agent.venue.rankings import acronym_exists

_CACHE_FILE = Path(__file__).resolve().parents[4] / ".cache" / "venue_acronyms.json"

_SYSTEM = (
    "You map academic venue names to their canonical CORE conference acronym — e.g. "
    "'Advances in Neural Information Processing Systems' -> 'NeurIPS', "
    "'IEEE Conference on Computer Vision and Pattern Recognition' -> 'CVPR'. "
    "Return the acronym only for a well-known ranked CONFERENCE; return null for journals, "
    "workshops, unknown venues, or whenever you are not confident. Do not guess."
)


class VenueMatch(BaseModel):
    # Defaults make the batch robust to the model dropping a field (common for journals,
    # where it just omits the acronym) — a missing acronym degrades to "NONE" (no venue score).
    name: str = Field(default="", description="the venue name exactly as given")
    acronym: str = Field(
        default="NONE",
        description=(
            "the canonical CORE conference acronym (e.g. 'NeurIPS', 'CVPR'), or the string "
            "'NONE' for a journal, workshop, unknown venue, or when you are not confident"
        ),
    )


class VenueMatches(BaseModel):
    venues: List[VenueMatch]


def _load_cache() -> Dict[str, Optional[str]]:
    if _CACHE_FILE.exists():
        try:
            return json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_cache(cache: Dict[str, Optional[str]]) -> None:
    _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _CACHE_FILE.write_text(json.dumps(cache, indent=2, ensure_ascii=False), encoding="utf-8")


def resolve_venues(names: List[str]) -> Dict[str, Optional[str]]:
    """Batched venue-name -> CORE acronym (grounded + cached); unknown/unsure -> None."""
    cache = _load_cache()
    uncached = sorted({n for n in names if n and n not in cache})
    if uncached:
        listing = "\n".join(f"{i}. {n}" for i, n in enumerate(uncached, 1))
        result: VenueMatches = chat_with_structured_output(
            get_llm("cheap"), _SYSTEM, f"Venues:\n{listing}", VenueMatches
        )

        # Align positionally (robust to the model reformatting the echoed name); only trust
        # the batch if the counts match, else leave them uncached to retry next run.
        if len(result.venues) == len(uncached):
            for name, match in zip(uncached, result.venues):
                ac = (match.acronym or "").strip()
                # ground against CORE: drop 'NONE' and any acronym not in the list
                cache[name] = ac if (ac.upper() != "NONE" and acronym_exists(ac)) else None
            _save_cache(cache)
    return {n: cache.get(n) for n in names}
