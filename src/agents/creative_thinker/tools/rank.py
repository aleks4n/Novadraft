"""Ranking arithmetic for the generation batch — no model involved.

:func:`rank_candidates` orders a deduplicated batch. It is **not** a quality filter
(the human is the evaluator) — it is a presentation order, so the most distinct and
best-grounded candidates are read first.

Refinement has no arithmetic left here. It used to: pairwise sibling votes were
turned into round-robin win rates by a ``tournament`` function, which could crown a
branch no single comparison had endorsed. One selector call over the whole frontier
decides directly now (``subagents/select_branch``), so there is nothing to add up.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.agents.creative_thinker.tools.text import similarity


@dataclass
class RankedCandidate:
    index: int
    score: float
    distinctness: float
    rating: float


def rank_candidates(texts: list[str], context: list[str] | None = None,
                    ratings: dict[int, float] | None = None) -> list[RankedCandidate]:
    """Order a deduplicated batch, most promising first.

    ``distinctness`` = 1 − the highest similarity to any sibling or context idea.
    ``rating`` is the optional grounded rating an Investigator pass attached — a
    *rating*, never a filter: it shifts the order, it never removes a candidate.
    Ties fall back to the original order, so the ranking is stable.
    """
    context = context or []
    ratings = ratings or {}
    out: list[RankedCandidate] = []
    for i, text in enumerate(texts):
        others = [t for j, t in enumerate(texts) if j != i] + context
        nearest = max((similarity(text, o) for o in others), default=0.0)
        distinctness = 1.0 - nearest
        rating = float(ratings.get(i, 0.5))
        out.append(RankedCandidate(
            index=i,
            score=round(0.6 * distinctness + 0.4 * rating, 4),
            distinctness=round(distinctness, 4),
            rating=rating,
        ))
    return sorted(out, key=lambda c: (-c.score, c.index))
