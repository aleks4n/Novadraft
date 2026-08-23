"""score_impact — blends the gap's per-paper signals into an impact score in [0, 1].

    impact = quality(venue, citation) * recency_decay * evidence_bonus

- quality: venue + citation blend (a neutral prior when neither is known)
- recency_decay: soft decay from the newest evidence paper's year
- evidence_bonus: multiplier >= 1, rewards gaps corroborated by multiple evidence papers

Per-paper facts (venue, year) are read from paper.json via paper_access, keyed by
(category_slug, paper_id). `compute_impact` is the reusable core: the node wraps it, and the
director re-runs it (via ranking_agent.agent.rescore_impact) to cheaply re-score impact alone
when it merges evidence into an already-ranked gap.
"""

from __future__ import annotations

import datetime as dt
from typing import List, Optional, Tuple

from src.common.schemas.gap import GapRecord
from src.common.tools.paper_access import get_venue, get_year
from src.agents.ranking_agent.schemas import ImpactBreakdown, RankingConfig
from src.agents.ranking_agent.state import RankingState
from src.agents.ranking_agent.venue import resolve_venues, score_for_acronym

_UNKNOWN_QUALITY = 0.5  # neutral prior when no venue/citation signal is available yet


def _venue_names(gap: GapRecord, category_slug: str) -> List[str]:
    """The gap's evidence papers' venue names (for one batched, cached resolve)."""
    names = set()
    for ev in gap.evidence:
        venue = get_venue(category_slug, ev.paper_id)
        if venue:
            names.add(venue)
    return sorted(names)


def _quality(venue_score: Optional[float], citation_score: Optional[float], config: RankingConfig) -> float:
    """Venue+citation blended into a [0,1] quality, re-normalized over present signals.

    Falls back to a neutral prior when neither is known yet.
    """
    parts: List[Tuple[float, float]] = []
    if venue_score is not None:
        parts.append((config.w_venue, venue_score))
    if citation_score is not None:
        parts.append((config.w_citation, citation_score))
    wsum = sum(w for w, _ in parts)
    return sum(w * v for w, v in parts) / wsum if wsum > 0 else _UNKNOWN_QUALITY


def _evidence_bonus(gap: GapRecord, config: RankingConfig) -> float:
    """Multiplier >= 1: 1.0 for a single evidence paper (no penalty), rising with
    diminishing returns toward 1 + evidence_bonus as more papers corroborate the gap."""
    extra = max(len(gap.evidence) - 1, 0)  # 0 for the common single-evidence case
    return 1.0 + config.evidence_bonus * (1.0 - 0.5 ** extra)


def _recency_factor(years: List[int], config: RankingConfig) -> float:
    """Soft decay from the newest evidence year: floor + (1-floor)*0.5**(age/half_life); 1.0 if none.

    Reasons in years (paper.json's year granularity, see paper_access.get_year), plenty for a
    multi-year half-life.
    """
    if not years:
        return 1.0
    age_years = max(0.0, (dt.date.today() - dt.date(max(years), 1, 1)).days / 365.25)
    return config.recency_floor + (1.0 - config.recency_floor) * 0.5 ** (age_years / config.recency_half_life)


def compute_impact(gap: GapRecord, category_slug: str, config: RankingConfig) -> ImpactBreakdown:
    """Impact for one gap: resolve its evidence venues (cached), then blend venue / recency /
    evidence, keeping the sub-components. Venue is the 'best' (max) score across the gap's
    evidence papers — a gap is impactful if at least one strong paper raises it.

    Deterministic apart from the cached venue resolve (no reasoner call), so it's cheap enough
    for the director to re-run on an evidence merge.
    """
    acronyms = resolve_venues(_venue_names(gap, category_slug))
    venue_scores: List[float] = []
    acronyms_found: List[str] = []
    years: List[int] = []
    for ev in gap.evidence:
        venue = get_venue(category_slug, ev.paper_id)
        year = get_year(category_slug, ev.paper_id)
        acronym = acronyms.get(venue) if venue else None
        vs = score_for_acronym(acronym, year)
        if vs is not None:
            venue_scores.append(vs)
            if acronym:
                acronyms_found.append(acronym)
        if year is not None:
            years.append(year)

    venue_score = max(venue_scores) if venue_scores else None
    citation = None  # no source yet — add a paper_access.get_citation when OpenAlex enrichment ships
    quality = _quality(venue_score, citation, config)
    recency = _recency_factor(years, config)
    ev_bonus = _evidence_bonus(gap, config)
    score = max(0.0, min(1.0, quality * recency * ev_bonus))
    return ImpactBreakdown(
        score=score, quality=quality, venue_score=venue_score, citation=citation,
        recency=recency, evidence_bonus=ev_bonus, venues=sorted(set(acronyms_found)),
    )


def score_impact(state: RankingState) -> dict:
    """LangGraph node: gap -> {"impact": ImpactBreakdown}."""
    return {"impact": compute_impact(state["gap"], state["category_slug"], state["config"])}
