"""RRF step (deterministic) — fuse the per-query result lists into one ranking."""

from __future__ import annotations

from collections import defaultdict
from typing import Dict, List

from src.common.schemas.paper import CandidatePaper
from src.agents.validator_agent.state import ValidatorState

_RRF_K = 10
_TOP_N = 15  # fused candidates to keep


def rrf_fuse(
    ranked_lists: List[List[CandidatePaper]], k: int = _RRF_K, top_n: int = _TOP_N
) -> List[CandidatePaper]:
    """Fuse ranked candidate lists by Reciprocal Rank Fusion. Pure, no I/O."""
    scores: Dict[str, float] = defaultdict(float)
    seen: Dict[str, CandidatePaper] = {}
    for papers in ranked_lists:
        for rank, paper in enumerate(papers, start=1):
            scores[paper.paper_id] += 1.0 / (k + rank)
            seen.setdefault(paper.paper_id, paper)  # keep first candidate seen per id
    ranked = sorted(seen.values(), key=lambda p: scores[p.paper_id], reverse=True)
    return ranked[:top_n]


def rrf(state: ValidatorState) -> dict:
    """LangGraph node: fuse search_results -> {"candidates": top-N ranked candidates}."""
    return {"candidates": rrf_fuse(state["search_results"])}
