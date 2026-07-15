"""Process-global rate limiting for external services.

`wait_turn(service)` blocks until the service's minimum interval has elapsed
since the previous request to it, then claims the slot. Every call site for a
service shares the same gate (e.g. the validator's arXiv search and
paper_fetch's arXiv downloads), so the spacing holds across threads and agents
— parallel gap validation included.

Only correct while the whole pipeline runs in ONE process: separate processes
can't see each other's timestamps (a bash loop of runs will still trip arXiv's
limit). Thread-safe via a single lock.
"""
from __future__ import annotations

import threading
import time

# service -> minimum seconds between requests
_MIN_INTERVAL = {
    "arxiv": 3.0,  # arXiv asks for ~1 request per 3s; exceeding it earns 503s/429s
}

_lock = threading.Lock()
_next_free: dict[str, float] = {}  # service -> earliest monotonic time of the next slot


def wait_turn(service: str) -> None:
    """Block until `service` may be called again, then claim the slot.

    Concurrent callers queue fairly: each claim pushes the next free slot one
    interval further out, so N threads arriving at once get spaced one interval
    apart rather than racing when the current wait expires.
    """
    interval = _MIN_INTERVAL[service]
    with _lock:
        now = time.monotonic()
        slot = max(_next_free.get(service, now), now)
        _next_free[service] = slot + interval
    wait = slot - time.monotonic()
    if wait > 0:
        time.sleep(wait)
