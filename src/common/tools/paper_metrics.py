"""Paper impact-metrics access (INTERIM stub — real data comes from paper.json `metrics`).

The ranking agent's impact scoring needs per-paper citation + venue signals. These are
produced upstream at fetch time (one OpenAlex call) and stored in paper.json's `metrics`
block; this getter loads them by paper_id. Until survey_gap ships that enrichment, the
stub returns None and the impact node falls back to the signals it can derive from the
gap itself (recency, evidence count).

`citation_normalized_percentile` is OpenAlex's age/field-normalized percentile in [0, 1]
(0.5 ~ "cited as expected for its age"); None when the paper is too new to have a cohort.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class PaperMetrics(BaseModel):
    venue: Optional[str] = None
    citation_normalized_percentile: Optional[float] = None  # OpenAlex, [0,1]; None if too new
    publication_date: Optional[str] = None  # YYYY-MM-DD (recency also derivable from gap.evidence)


def get_paper_metrics(paper_id: str) -> Optional[PaperMetrics]:
    """Return a paper's impact metrics, or None if unavailable.

    INTERIM: the real implementation reads paper.json's `metrics` block (populated by
    survey_gap's OpenAlex enrichment — see validator SPEC.md open items). For now this
    returns None, so the impact node leans on gap-derived signals.
    """
    return None
