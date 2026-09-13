"""The director_agent graph state — what flows between its top-level nodes.

A LangGraph ``TypedDict`` (``total=False``, so each node returns only the keys it
sets). Linear flow: load the master list, collect the pending inbox, dedup each
pending gap against the known gaps, finalize.

The director communicates with the rest of the pipeline through the workspace
filesystem (``gaps/<stage>/``), so this state carries control info only, not the
gap payloads. ``novel_gap_ids`` is the handoff the pipeline graph fans out to the
validator; ``result`` is what ``run()`` returns.
"""
from __future__ import annotations

from typing import TypedDict


class DirectorState(TypedDict, total=False):
    # inputs
    category_slug: str
    # Scope: restrict which pending gaps to triage. Explicit ``scope_gap_ids`` wins;
    # else fall back to ``paper_id``'s gaps (so an ingest/resume is *per paper*, not
    # the whole category inbox). Neither set → triage the entire pending/ inbox.
    scope_gap_ids: list[str]
    paper_id: str

    # per-stage working sets (lists of (stage, record) or raw records)
    known_gaps: list[dict]      # already-known gaps: master + to_validate + to_rank
    pending_gaps: list[dict]    # freshly mined gaps drained from gaps/pending/

    # outputs
    novel_gap_ids: list[str]        # moved pending -> to_validate (feed the validator)
    duplicate_gap_ids: list[str]    # merged into a known gap, then archived
    result: dict
