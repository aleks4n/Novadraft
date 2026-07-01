"""The summarize subgraph's state — what flows between its nodes.

A LangGraph TypedDict; `total=False` so each node returns only the keys it sets.
The flow is a bounded extractor↔critic cycle:

    enter → extractor → critic → (route: regenerate? → extractor : → finalize) → END

The loop-carry keys (attempt, feedback, persisted issues/hints, prev_signature,
regenerated, critique) are exactly the locals the old imperative
`summarize.summarize()` `while` loop carried between iterations — moved into
state so the cycle's nodes can read/update them.

This graph is also mounted as a node inside the parent `paper_agent` graph, so
its state shares keys with `PaperState`. The shared keys are the inputs
(`category_slug`/`paper_id`/`force`) and the single output `summary_result`,
which flows back into the parent. Everything else is subgraph-private. In
particular this stage's own extractor output is `summarize_extract`, NOT
`extract_result` — `PaperState.extract_result` is the parent's section
extraction, and a shared name would clobber it on the way out.
"""
from __future__ import annotations

from typing import List, TypedDict


class SummarizeState(TypedDict, total=False):
    # inputs (shared with the parent PaperState when mounted as a node)
    category_slug: str
    paper_id: str
    force: bool
    max_loops: int

    # loop carry (subgraph-private — not in PaperState, so never written back)
    attempt: int
    feedback: List[dict] | None
    open_hint_texts: List[str]
    persisted_issues: List[dict]
    persisted_hints: List[dict]
    prev_signature: tuple | None
    regenerated: bool
    critique: dict | None
    summarize_extract: dict   # this stage's extractor output (was extract_result)
    done: bool                # the loop's break flag, set by extractor/critic
    skip: bool                # already-verified short-circuit (no finalize/write)

    # output (shared key — written back into the parent PaperState)
    summary_result: dict      # final return value of summarize()
