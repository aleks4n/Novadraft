"""score_impact — blends per-paper impact signals into [0, 1].

    impact = quality(venue, citation) * recency_decay * evidence_bonus

- quality: venue + citation blend
- recency_decay: soft decay from the gap's newest evidence date    
- evidence_bonus: multiplier >= 1, rewards gaps with mutliple evidence papers"""

from __future__ import annotations

import datetime as dt
from typing import Dict, List, Optional, Tuple

from src.common.schemas.gap import Gap
from src.common.tools.paper_metrics import get_paper_metrics
from src.agents.ranking_agent.schemas import ImpactBreakdown, RankingConfig
from src.agents.ranking_agent.state import RankingState
from src.agents.ranking_agent.venue import resolve_venues, score_for_acronym

_UNKNOWN_QUALITY = 0.5  # neutral prior when no venue/citation signal is available yet


def _year(date: Optional[str]) -> Optional[int]:
    """Year from a 'YYYY-MM-DD' (or 'YYYY...') date string; None if unusable."""
    if date and len(date) >= 4 and date[:4].isdigit():
        return int(date[:4])
    return None


def _collect_venue_names(gaps: List[Gap]) -> List[str]:
    """Every unique venue name across the gaps' evidence papers (for one batched resolve)."""
    names = set()
    for gap in gaps:
        for ev in gap.evidence:
            m = get_paper_metrics(ev.paper_id)
            if m and m.venue:
                names.add(m.venue)
    return sorted(names)


def _quality(venue_score: Optional[float], citation_score: Optional[float], config: RankingConfig) -> float:
    """Venue+citation blended into a [0,1] quality, re-normalized over present signals.

    Falls back to a neutral prior when neither is known yet (the interim stub state).
    """
    parts: List[Tuple[float, float]] = []
    if venue_score is not None:
        parts.append((config.w_venue, venue_score))
    if citation_score is not None:
        parts.append((config.w_citation, citation_score))
    wsum = sum(w for w, _ in parts)
    return sum(w * v for w, v in parts) / wsum if wsum > 0 else _UNKNOWN_QUALITY


def _evidence_bonus(gap: Gap, config: RankingConfig) -> float:
    """Multiplier >= 1: 1.0 for a single evidence paper (no penalty), rising with
    diminishing returns toward 1 + evidence_bonus as more papers corroborate the gap."""
    extra = max(len(gap.evidence) - 1, 0)  # 0 for the common single-evidence case
    return 1.0 + config.evidence_bonus * (1.0 - 0.5 ** extra)


def _recency_factor(gap: Gap, config: RankingConfig) -> float:
    """
    Soft decay from the gap's newest evidence date.
    Factor = floor + (1 - floor) * 0.5 ** (age_years / half_life); 1.0 if no usable date.
    """
    
    dates = []
    for ev in gap.evidence:
        if not ev.published_date:
            continue
        try:
            dates.append(dt.date.fromisoformat(ev.published_date))
        except ValueError:
            continue
    if not dates:
        return 1.0
    age_years = max(0.0, (dt.date.today() - max(dates)).days / 365.25)
    return config.recency_floor + (1.0 - config.recency_floor) * 0.5 ** (age_years / config.recency_half_life)


def _impact(gap: Gap, config: RankingConfig, acronyms: Dict[str, Optional[str]]) -> ImpactBreakdown:
    """impact = quality(venue, citation) * recency * evidence_bonus, with the sub-components kept.

    Venue + citation are the 'best' (max) across the gap's evidence papers — a gap is impactful
    if at least one strong paper raises it.
    """
    venue_scores: List[float] = []
    acronyms_found: List[str] = []
    cites: List[float] = []
    for ev in gap.evidence:
        m = get_paper_metrics(ev.paper_id)
        if m is None:
            continue
        ac = acronyms.get(m.venue) if m.venue else None
        vs = score_for_acronym(ac, _year(m.publication_date))
        if vs is not None:
            venue_scores.append(vs)
            if ac:
                acronyms_found.append(ac)
        if m.citation_normalized_percentile is not None:
            cites.append(m.citation_normalized_percentile)

    venue_score = max(venue_scores) if venue_scores else None
    citation = max(cites) if cites else None
    quality = _quality(venue_score, citation, config)
    recency = _recency_factor(gap, config)
    ev_bonus = _evidence_bonus(gap, config)
    score = max(0.0, min(1.0, quality * recency * ev_bonus))
    return ImpactBreakdown(
        score=score, quality=quality, venue_score=venue_score, citation=citation,
        recency=recency, evidence_bonus=ev_bonus, venues=sorted(set(acronyms_found)),
    )


def score_impact(state: RankingState) -> dict:
    """LangGraph node: openness survivors -> {"impact": {gap_id: ImpactBreakdown}} (>= min_impact)."""
    config = state["config"]
    openness = state.get("openness", {})  # only gaps that passed score_openness
    gaps_by_id = {g.gap_id: g for g in state.get("gaps", [])}
    survivors = [gaps_by_id[gid] for gid in openness if gid in gaps_by_id]

    # Phase 1: resolve every venue name to a CORE acronym in ONE batched (cached) LLM call.
    acronyms = resolve_venues(_collect_venue_names(survivors))

    # Phase 2: score each surviving gap.
    impact: Dict[str, ImpactBreakdown] = {}
    for gap in survivors:
        bd = _impact(gap, config, acronyms)
        if bd.score >= config.min_impact:
            impact[gap.gap_id] = bd
    return {"impact": impact}
