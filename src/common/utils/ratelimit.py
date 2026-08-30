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
    # Semantic Scholar: a free API key grants ~1 req/s. Unauthenticated calls share one
    # global pool and 429 constantly, so the same gate applies either way and the caller's
    # 429 handling (defer) widens it when the server pushes back.
    "s2": 1.1,
    "openalex": 0.15,  # OpenAlex allows 10 req/s in the polite pool (mailto identified)
}

_lock = threading.Lock()
_next_free: dict[str, float] = {}  # service -> earliest monotonic time of the next slot


def wait_turn(service: str) -> None:
    """Block until `service` may be called again, then claim the slot.

    Concurrent callers re-check the shared deadline before claiming a turn, so
    they stay spaced and also observe cooldowns added while they were waiting.
    """
    interval = _MIN_INTERVAL[service]
    # Re-check after sleeping instead of reserving a future slot. A server-side
    # 429 may extend the shared cooldown while callers are queued; reservations
    # made before that response would otherwise wake up and keep hammering the
    # service during its requested quiet period.
    while True:
        with _lock:
            now = time.monotonic()
            ready_at = _next_free.get(service, now)
            if now >= ready_at:
                _next_free[service] = now + interval
                return
            wait = ready_at - now
        time.sleep(wait)


def defer(service: str, seconds: float) -> None:
    """Pause all callers of ``service`` for at least ``seconds``.

    Used after a shared rate-limit response. Waiting callers re-check this value
    before making their request, so one 429 cools down the whole process rather
    than only the thread that happened to receive it.
    """
    until = time.monotonic() + max(0.0, seconds)
    with _lock:
        _next_free[service] = max(_next_free.get(service, 0.0), until)
