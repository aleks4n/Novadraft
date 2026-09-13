"""The creative thinker's graph state — what flows between its nodes.

``total=False`` so each node returns only the keys it sets.

- The working copy (``idea``) is a deep copy of the caller's snapshot, made in
  ``prepare``. Every content edit lands on that copy; the caller's object is never
  touched.
- ``trace`` and ``ledger`` are live objects mutated in place as the run goes, not
  values passed between nodes. They are returned once by ``prepare`` and then
  written through, so the thinking graph and the budget stay in one place even
  when a subgraph node owns the loop.

Nothing here is persisted mid-run; only ``assemble`` produces something the caller
can keep. ``summary`` is built by the nodes, since only they know why a branch was
dropped.
"""

from __future__ import annotations

from typing import Any, TypedDict

from src.agents.creative_thinker.schemas import (
    OpenQuestion,
    ProposedChange,
    SuggestedOperation,
    ThinkerCall,
)
from src.agents.creative_thinker.tools.budget import BudgetLedger
from src.agents.creative_thinker.tools.trace import TraceBuilder
from src.common.schemas.idea import IdeaRecord, RunSummary


class CreativeThinkerState(TypedDict, total=False):
    # --- inputs ---
    spec: ThinkerCall  # the six slots the orchestrator filled, plus budget
    investigator: Any  # callable(question)->dict, or None. Never imported, only injected.

    # --- run-scoped objects (created by prepare, mutated in place) ---
    idea: IdeaRecord  # the working copy — the only idea this run may change
    trace: TraceBuilder
    ledger: BudgetLedger

    # --- rendered prompt material (deterministic, built once) ---
    context_md: str
    idea_md: str
    prior_ground: str
    existing_ideas: list[str]  # workspace ideas in view — the second dedup target

    # --- generation ---
    drafts: list[IdeaRecord]

    # --- refinement ---
    depth: int  # completed rounds
    path: list[str]  # node ids of the surviving branch, root first
    frontier: list[str]  # the siblings created by the last expansion
    findings: list[str]  # answers the Investigator returned on this path

    # --- outcome (both modes) ---
    operations: list[SuggestedOperation]
    proposed_changes: list[ProposedChange]  # recommended, not applied — refinement only
    questions: list[OpenQuestion]
    status: str  # completed | stopped-uncertain | truncated
    status_reason: str
    summary: RunSummary  # the compressed run — what the *next* run will read
    answer: str  # the reply to spec.task; refinement only, may be empty
    rejected_notes: list[str]  # what was dropped, collected for the summary

    # --- the return value ---
    # The envelope as JSON — a top-level ``result`` delta is what the run log
    # records as the run's output, so the graph returns the same bytes the
    # harness prints and a replay reads back.
    result: dict
