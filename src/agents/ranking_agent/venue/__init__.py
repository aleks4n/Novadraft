"""Venue scoring for the ranking agent.

Public API: resolve a messy venue name to a CORE acronym (LLM, cached) and score that
acronym against the year-appropriate CORE edition.
"""

from src.agents.ranking_agent.venue.rankings import acronym_exists, score_for_acronym
from src.agents.ranking_agent.venue.resolver import resolve_venues

__all__ = ["resolve_venues", "score_for_acronym", "acronym_exists"]
