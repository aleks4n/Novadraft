"""Deduplication — against the batch *and* against the ideas already in context.

Generation has no LLM judge: candidates are not filtered for quality, only for
being the same idea twice. Two things count as "already there":

- another candidate in this fan-out (batch dedup), and
- an idea the plan loaded into the context (context dedup) — re-proposing an idea
  the workspace already holds is the failure mode this exists to prevent.

Deterministic on purpose: it is what the "``generation`` with ``n = k`` returns
``k`` deduplicated drafts" test actually asserts.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.agents.creative_thinker.tools.text import similarity

# Jaccard over stop-word-free token sets. 0.75 keeps genuinely different
# candidates that share a vocabulary (same field, same gap) and drops restatements.
DUPLICATE_THRESHOLD = 0.75


@dataclass
class DedupResult:
    kept: list[int] = field(default_factory=list)  # indices into the input list
    dropped: list[tuple[int, str]] = field(default_factory=list)  # (index, reason)


def dedupe(texts: list[str], existing: list[str] | None = None,
           threshold: float = DUPLICATE_THRESHOLD) -> DedupResult:
    """Keep the first occurrence of each distinct text.

    Order is preserved and the *earlier* candidate wins a collision, so the
    result does not depend on how the fan-out's threads happened to finish.
    """
    existing = existing or []
    result = DedupResult()
    for i, text in enumerate(texts):
        dup_of_context = next(
            (j for j, e in enumerate(existing) if similarity(text, e) >= threshold), None
        )
        if dup_of_context is not None:
            result.dropped.append((i, f"duplicate of context idea #{dup_of_context}"))
            continue
        dup_of_batch = next(
            (k for k in result.kept if similarity(text, texts[k]) >= threshold), None
        )
        if dup_of_batch is not None:
            result.dropped.append((i, f"duplicate of candidate #{dup_of_batch}"))
            continue
        result.kept.append(i)
    return result
