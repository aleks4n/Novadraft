"""The gap subgraph's state — what flows between its nodes.

A LangGraph TypedDict; `total=False` so each node returns only the keys it sets.
The flow is a bounded extractor↔critic cycle:

    enter → extractor → critic → (route: converged? → finalize : → extractor) → END

`ctx` is the one-time static context (sections, summary, metadata, inline
section names) the `enter` node reads from paper.json. `loop` is the mutable
cross-pass bookkeeping the old imperative loop carried in a single `state` dict
(accepted / rejected / retry_anchor / issues / missing_hints / warnings /
loop_trail / converged) — the extractor and critic nodes mutate it in place and
return it.
"""
from __future__ import annotations

from typing import TypedDict


class GapState(TypedDict, total=False):
    # inputs (shared with the parent PaperState when mounted as a node)
    category_slug: str
    paper_id: str
    force: bool
    max_loops: int

    # loop carry (subgraph-private — not in PaperState, so never written back)
    ctx: dict           # static per-paper context (set once by `enter`)
    loop: dict          # mutable cross-pass bookkeeping
    attempt: int
    skip: bool          # already-extracted short-circuit (no finalize/write)

    # output (shared key — written back into the parent PaperState)
    gaps_result: dict   # final return value of extract_gaps()
