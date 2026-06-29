"""The validator subgraph's state — what flows between its nodes.

A LangGraph TypedDict; `total=False` so each node returns only the keys it sets.
The linear flow: gap -> search_queries -> candidates -> shortlist -> assessments -> verdict.
"""

from __future__ import annotations

from typing import List, TypedDict

from src.common.schemas.gap import GapRecord
from src.common.schemas.paper import CandidatePaper
from src.common.schemas.verdict import PaperAssessment, Verdict
from src.agents.validator_agent.schemas import SearchQueries


class ValidatorState(TypedDict, total=False):
    gap: GapRecord  # input
    category_slug: str  # input: workspace partition, for paper.json getters (year, keywords)
    search_queries: SearchQueries  # after query_gen (LLM)
    search_results: List[List[CandidatePaper]]  # after search (one ranked list per query; transient)
    candidates: List[CandidatePaper]  # after rrf (fused, ranked, deduped)
    shortlist: List[CandidatePaper]  # after screen (ranked deep-read queue, <= ~8)
    assessments: List[PaperAssessment]  # after deep_read (<= 5)
    skipped_ids: List[str]  # shortlisted papers whose full text couldn't be fetched
    verdict: Verdict  # final output
