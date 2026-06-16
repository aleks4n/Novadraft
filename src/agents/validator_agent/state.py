"""The validator subgraph's state — what flows between its nodes.

A LangGraph TypedDict; `total=False` so each node returns only the keys it sets.
The linear flow: gap -> search_queries -> candidates -> shortlist -> assessments -> verdict.
"""

from __future__ import annotations

from typing import List, TypedDict

from src.common.schemas.gap import Gap
from src.common.schemas.paper import Paper
from src.common.schemas.verdict import PaperAssessment, Verdict
from src.agents.validator_agent.schemas import SearchQueries


class ValidatorState(TypedDict, total=False):
    gap: Gap  # input
    search_queries: SearchQueries  # after query_gen (LLM)
    search_results: List[List[Paper]]  # after search (one ranked list per query; transient)
    candidates: List[Paper]  # after rrf (fused, ranked, deduped)
    shortlist: List[Paper]  # after screen (ranked deep-read queue, <= ~8)
    assessments: List[PaperAssessment]  # after deep_read (<= 5)
    skipped_ids: List[str]  # shortlisted papers whose full text couldn't be fetched
    verdict: Verdict  # final output
