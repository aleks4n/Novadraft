"""Process-global work claims for gap processing.

Runs that sweep a stage folder (the pipeline's validate/rank loops, a resume run,
the validator's ``validate_all``) list the folder and then *claim* each gap before
processing it. A gap someone else already claimed is skipped — so two concurrent
runs (two paper ingests, an ingest overlapping a resume, a CLI batch next to an
app run) never process the same gap twice, without any coordination beyond this
registry.

Claims are keyed ``(category_slug, gap_id)`` and are stage-agnostic: whoever holds
the claim owns the gap through its current processing step and releases it when the
step finishes (the gap has then moved to another stage folder) or fails (the gap
stays put and becomes claimable again for a retry).

Like ``ratelimit``, this is only correct while everything runs in ONE process —
separate processes can't see each other's claims. Thread-safe via a single lock.
"""
from __future__ import annotations

import threading

_lock = threading.Lock()
_claimed: set[tuple[str, str]] = set()


def try_claim(category_slug: str, gap_id: str) -> bool:
    """Claim one gap. Returns False if another run already holds it."""
    key = (category_slug, gap_id)
    with _lock:
        if key in _claimed:
            return False
        _claimed.add(key)
        return True


def claim_batch(category_slug: str, gap_ids: list[str]) -> list[str]:
    """Atomically claim every unclaimed gap in ``gap_ids`` (preserving order) and
    return the ones this caller now owns. Gaps already claimed by another run are
    silently skipped — they're being handled."""
    with _lock:
        mine = [g for g in gap_ids if (category_slug, g) not in _claimed]
        _claimed.update((category_slug, g) for g in mine)
    return mine


def release(category_slug: str, gap_id: str) -> None:
    """Release a claim (idempotent — releasing an unheld claim is a no-op)."""
    _claimed.discard((category_slug, gap_id))  # set.discard is atomic under the GIL


def release_all(category_slug: str, gap_ids: list[str]) -> None:
    """Release a batch of claims (idempotent)."""
    with _lock:
        _claimed.difference_update((category_slug, g) for g in gap_ids)
