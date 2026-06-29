"""Section selection + truncation shared by the gap extractor and critic.

The extractor (which proposes gaps) and the critic (which judges them) must
read the same inlined sections, so the exclusion rule, the per-section char
budget, and the truncation helper live here as a tool both import — not
duplicated, not cross-imported between the two sibling subagents.
"""
from __future__ import annotations

# Sections excluded from extraction by a fixed rule — not a model decision.
EXCLUDED_SECTIONS = {"abstract", "related_work", "related work"}

# Char budget per inlined section — a safety net against a pathological
# outlier section, not a routine cutoff. See summarize's section budgets: this
# agent re-reads the same raw sections, so it inherits the same
# truncation-hides-real-content risk.
DEFAULT_SECTION_CHAR_LIMIT = 20000


def select_gap_sections(section_names: list[str]) -> list[str]:
    """All sections Stage 2 extracted, minus the fixed exclusion list.

    Deliberately the "all except N" direction, not "search for the right
    N" — every section name actually present in this paper is included
    unless it matches EXCLUDED_SECTIONS by exact (case-insensitive) name.
    """
    return [n for n in section_names if n.strip().lower() not in EXCLUDED_SECTIONS]


def truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + " […truncated]"
