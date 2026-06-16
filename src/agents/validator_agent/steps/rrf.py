"""RRF step (deterministic) — fuse the per-query result lists into one ranking.

Reciprocal Rank Fusion: a paper's score is the sum of 1/(k + rank) over every
query-list it appears in (rank 1-based). The large k damps the top ranks, so a
paper recurring across queries outranks a one-list top hit. Dedupe is intrinsic
(the same paper_id across queries accumulates into one entry); keep the top N.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Dict, List

from src.common.schemas.paper import Paper
from src.agents.validator_agent.state import ValidatorState

_RRF_K = 60  # rank-damping constant (standard RRF default)
_TOP_N = 15  # fused candidates to keep


def rrf_fuse(ranked_lists: List[List[Paper]], k: int = _RRF_K, top_n: int = _TOP_N) -> List[Paper]:
    """Fuse ranked Paper lists by Reciprocal Rank Fusion. Pure, no I/O."""
    scores: Dict[str, float] = defaultdict(float)
    seen: Dict[str, Paper] = {}
    for papers in ranked_lists:
        for rank, paper in enumerate(papers, start=1):
            scores[paper.paper_id] += 1.0 / (k + rank)
            seen.setdefault(paper.paper_id, paper)  # keep first Paper seen per id
    ranked = sorted(seen.values(), key=lambda p: scores[p.paper_id], reverse=True)
    return ranked[:top_n]


def rrf(state: ValidatorState) -> dict:
    """LangGraph node: fuse search_results -> {"candidates": top-N ranked Papers}."""
    return {"candidates": rrf_fuse(state["search_results"])}
