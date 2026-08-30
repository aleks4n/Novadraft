"""score_impact — blends the gap's per-paper signals into an impact score in [0, 1].

    impact = quality(venue, citation) * recency_decay * evidence_bonus

- quality: venue + citation blend (a neutral prior when neither is known)
- recency_decay: soft decay from the newest evidence paper's year
- evidence_bonus: multiplier >= 1, rewards gaps corroborated by multiple evidence papers

Per-paper facts (venue, year, citations) are read from paper.json via paper_access, keyed by
(category_slug, paper_id), and are populated by paper_fetch/enrich.py. `compute_impact` is the
reusable core: the node wraps it, and the director re-runs it (via
ranking_agent.agent.rescore_impact) to cheaply re-score impact alone when it merges evidence
into an already-ranked gap.

Venue resolution prefers the acronym the enrichment provider reported over the LLM
name->acronym resolver, and refuses to score a venue the provider marked as a workshop:
"4th Deployable AI Workshop at AAAI 2026" resolves to AAAI by name, which would award a
workshop paper the flagship conference's A* score.
"""

from __future__ import annotations

import datetime as dt
import math
from typing import List, Optional, Tuple

from src.common.schemas.gap import GapRecord
from src.common.tools.paper_access import (
    get_citation_count,
    get_venue,
    get_venue_acronyms,
    get_venue_type,
    get_year,
)
from src.agents.ranking_agent.schemas import ImpactBreakdown, RankingConfig
from src.agents.ranking_agent.state import RankingState
from src.agents.ranking_agent.venue import resolve_venues, score_for_acronym

_UNKNOWN_QUALITY = 0.5  # neutral prior when no venue/citation signal is available yet

# Venue kinds that must not be scored against the CORE conference list. CORE ranks
# conferences; a workshop or journal matched to a conference acronym inherits a rank it
# never earned (see the module docstring).
_UNRANKABLE_VENUE_TYPES = {"workshop", "journal"}


def _venue_names(gap: GapRecord, category_slug: str) -> List[str]:
    """Venue names still needing the LLM resolver: those with no provider acronym and no
    disqualifying venue type (for one batched, cached resolve)."""
    names = set()
    for ev in gap.evidence:
        if get_venue_acronyms(category_slug, ev.paper_id):
            continue  # provider gave the acronym outright
        if (get_venue_type(category_slug, ev.paper_id) or "").lower() in _UNRANKABLE_VENUE_TYPES:
            continue
        venue = get_venue(category_slug, ev.paper_id)
        if venue:
            names.add(venue)
    return sorted(names)


def _paper_venue_score(category_slug: str, paper_id: str, year: Optional[int],
                       resolved: dict) -> Tuple[Optional[float], Optional[str]]:
    """(CORE score, acronym) for one paper, or (None, None) when it cannot be ranked.

    Provider acronyms win over the LLM resolver: they are exact, need no call, and come
    with the venue type that tells us whether ranking it is meaningful at all.
    """
    if (get_venue_type(category_slug, paper_id) or "").lower() in _UNRANKABLE_VENUE_TYPES:
        return None, None
    for acronym in get_venue_acronyms(category_slug, paper_id):
        score = score_for_acronym(acronym, year)
        if score is not None:
            return score, acronym
    venue = get_venue(category_slug, paper_id)
    acronym = resolved.get(venue) if venue else None
    return score_for_acronym(acronym, year), acronym


def _citation_score(count: Optional[int], year: Optional[int], config: RankingConfig) -> Optional[float]:
    """Citations -> [0,1], or None when the paper has never been enriched.

    Read as a rate (citations per year since publication) so a 2015 paper is not rewarded
    simply for having had a decade to accumulate them, then log-compressed because
    citation counts span orders of magnitude. A count of 0 is a real signal (an ignored
    paper), not a missing one, so only None means unknown.
    """
    if count is None:
        return None
    if year is None:
        age = 1.0
    else:
        age = max(0.5, (dt.date.today() - dt.date(year, 1, 1)).days / 365.25)
    per_year = count / age
    return min(1.0, math.log10(1.0 + per_year) / math.log10(1.0 + config.citations_per_year_full))


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
    diminishing returns toward 1 + evidence_bonus as more papers corroborate the gap.

    Counts DISTINCT papers, not evidence entries: one paper quoted twice is one
    corroborating source, so it must not earn the multi-paper bonus.
    """
    extra = max(len({ev.paper_id for ev in gap.evidence}) - 1, 0)  # 0 for the common single-paper case
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
    resolved = resolve_venues(_venue_names(gap, category_slug))
    venue_scores: List[float] = []
    citation_scores: List[float] = []
    acronyms_found: List[str] = []
    years: List[int] = []
    for paper_id in {ev.paper_id for ev in gap.evidence}:
        year = get_year(category_slug, paper_id)
        vs, acronym = _paper_venue_score(category_slug, paper_id, year, resolved)
        if vs is not None:
            venue_scores.append(vs)
            if acronym:
                acronyms_found.append(acronym)
        cs = _citation_score(get_citation_count(category_slug, paper_id), year, config)
        if cs is not None:
            citation_scores.append(cs)
        if year is not None:
            years.append(year)

    # 'best paper' semantics on both signals: a gap is impactful if at least one strong
    # paper raises it, so a weak companion paper must not drag the score down.
    venue_score = max(venue_scores) if venue_scores else None
    citation = max(citation_scores) if citation_scores else None
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
