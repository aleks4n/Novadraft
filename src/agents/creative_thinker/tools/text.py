"""Bag-of-words similarity — the one text measure dedup, ranking and diversity share.

Deliberately not a model call and not an embedding: dedup, ranking arithmetic and
the diversity metrics have to be *deterministic* for the test suite to mean
anything, and cheap enough to run on every pair in a fan-out.
"""

from __future__ import annotations

import re

_WORD = re.compile(r"[a-z0-9]+")

# Stop words only — anything domain-specific would silently bias what counts as
# a duplicate, and this measure is used to drop candidates.
_STOP = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "in", "into",
    "is", "it", "its", "of", "on", "or", "that", "the", "then", "this", "to",
    "we", "with", "which", "was", "were", "will", "can", "using", "use",
}


def tokens(text: str) -> set[str]:
    """Lowercased word set, stop words and 1-char tokens dropped."""
    return {
        w for w in _WORD.findall((text or "").lower())
        if len(w) > 1 and w not in _STOP
    }


def similarity(a: str, b: str) -> float:
    """Jaccard overlap of the two token sets, in [0, 1]. Two empty texts are
    treated as identical (1.0) — that is what a mock/offline model produces, and
    collapsing them is the right call."""
    ta, tb = tokens(a), tokens(b)
    if not ta and not tb:
        return 1.0
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def distance(a: str, b: str) -> float:
    return 1.0 - similarity(a, b)
