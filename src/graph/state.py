"""State for the full ingestion pipeline graph (``src/graph/pipeline.py``).

A LangGraph TypedDict; ``total=False`` so each node returns only the keys it sets.
The three agents mounted in the pipeline (paper / director / validator) talk to
each other through the workspace filesystem (``gaps/<stage>/``), so this state is
thin: the shared inputs, a few control flags the paper subgraph already sets, the
director's ``novel_gap_ids`` handoff, and a reducer-backed ``verdicts`` list the
fanned-out validator branches accumulate into.
"""
from __future__ import annotations

import operator
from typing import Annotated, TypedDict


class PipelineState(TypedDict, total=False):
    # inputs (shared verbatim with paper_agent's keys, so the subgraph reads them)
    category_slug: str
    query: str
    force: bool

    # produced by the paper subgraph (control + gap handoff)
    paper_id: str
    ambiguous: bool
    needs_source: bool
    gaps_result: dict

    # director output — the novel gaps to validate
    novel_gap_ids: list[str]

    # resume plan (see build_resume_graph): scope the tail to one gap (``gap_id``)
    # or one paper's gaps (``paper_id``); ``scope_gap_ids`` is the resolved target
    # set ([] = the whole to_validate backlog).
    scope_gap_ids: list[str]

    # single-gap resume input (which gap to resume)
    gap_id: str

    # sequential work queue — reused across the two loop phases (validate, then
    # rank). Each phase's enqueue seeds it; the loop node pops the front and loops
    # back until it's empty (one gap at a time, so the arXiv prior-art search isn't
    # hammered in parallel).
    queue: list[str]

    # results accumulated across the sequential passes
    verdicts: Annotated[list[dict], operator.add]   # validator: solved / open / partial
    ranked: Annotated[list[dict], operator.add]     # ranking: open opportunities scored

    result: dict
