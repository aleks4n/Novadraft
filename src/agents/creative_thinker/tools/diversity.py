"""Diversity measures for the run's metrics.

Styles are the diversity mechanism, so diversity is the number that says whether
the roster is doing its job. Both measures are deterministic and text-only, which
makes them comparable across runs and across style rosters over time.
"""

from __future__ import annotations

from src.agents.creative_thinker.tools.text import distance, similarity

# Above this, two thoughts are "the same cluster" — looser than the dedup
# threshold, which asks the stricter question of whether they are the same idea.
CLUSTER_THRESHOLD = 0.5


def mean_pairwise_distance(texts: list[str]) -> float:
    """Average 1 − Jaccard over all unordered pairs. 0.0 for < 2 texts."""
    if len(texts) < 2:
        return 0.0
    pairs = [
        distance(texts[i], texts[j])
        for i in range(len(texts)) for j in range(i + 1, len(texts))
    ]
    return round(sum(pairs) / len(pairs), 4)


def cluster_count(texts: list[str], threshold: float = CLUSTER_THRESHOLD) -> int:
    """Single-link clustering by transitive similarity — how many genuinely
    different directions the run explored, as opposed to how many thoughts it
    produced."""
    clusters: list[list[str]] = []
    for text in texts:
        hit = next(
            (c for c in clusters if any(similarity(text, m) >= threshold for m in c)),
            None,
        )
        if hit is None:
            clusters.append([text])
        else:
            hit.append(text)
    return len(clusters)
