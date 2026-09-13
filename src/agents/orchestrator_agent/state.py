"""The orchestrator graph state — what flows between its nodes.

A LangGraph ``TypedDict``; ``total=False`` so each node returns only the keys it
sets.

    assemble_context -> plan -> execute -> reflect -> finish
             |           | ^        |                  ^
             |           | +--------+  (replan/cont.)  |
             |           +-- plan called no tools -----+
             |           +-- awaiting approval --> propose -> END
             +-- an approved plan came in --> adopt_plan --> execute

Approving a plan is not part of this graph: the run that proposed one ends at
``propose``, and the user's Start button begins a second run carrying
``approved_plan``.

Accepting an idea is not part of this graph either — it needs no LLM, so it is a
plain function call (``agent.commit_selection``).
"""

from __future__ import annotations

from typing import List, TypedDict

from src.common.schemas.context import Context
from src.common.schemas.idea import IdeaRecord
from src.agents.orchestrator_agent.schemas import (
    FocusSelection,
    Plan,
    ReflectJudgment,
    ToolResult,
)


MAX_ITERATIONS = 3
"""Reflect loops before `finish` is forced. Lives here rather than in `agent.py` because
reflect is told which round it is on, and importing the graph module would be circular."""

MAX_LLM_CALLS_PER_TURN = 120
"""What one user turn may spend across every subagent it calls.

A subagent's own ceiling is per call — the right unit for it, the wrong one for a turn: a
two-step plan over three reflect rounds multiplies it six times over, and neither component
is wrong about its own limit. This is the number nobody owned, so the orchestrator owns it
and hands each step a slice of what is left.

Provisional. Pick it properly once there are real runs to look at."""

UNMETERED_TOOL_LLM_CHARGE = 12
"""Conservative charge for a tool that cannot report its own LLM usage.

Charging the complete grant makes a one-step investigator plan consume the entire turn,
so reflect can request a replan but `_route` immediately stops it.  A fixed reserve keeps
unmetered tools accountable while leaving room for the orchestrator's bounded retry loop.
"""


class OrchestratorState(TypedDict, total=False):
    # --- inputs, set at the boundary (run()) ---
    session_id: str
    category_slug: str
    run_id: str  # this turn; names the log file, the run result, and each idea's origin
    message: str  # the user's text
    focus: FocusSelection  # gap_ids + idea_ids clicked in the UI
    require_approval: bool  # stop after planning and offer the plan to the user
    approved_plan: Plan  # the plan the user started, possibly with edited arguments

    # --- after assemble_context (deterministic) ---
    context: Context
    history: List[dict]  # earlier turns of this session: {message, answer}, oldest first

    # --- after plan (LLM) ---
    plan: Plan

    # --- after execute (deterministic dispatch) ---
    results: List[ToolResult]
    carried: int  # how many of `results` predate this plan, so they aren't paired by index
    spent: int  # LLM calls this turn has used, across every subagent it called

    # --- after reflect (LLM) ---
    judgment: ReflectJudgment
    iteration: int  # incremented in reflect; the loop guard

    # --- after finish ---
    ideas: List[IdeaRecord]  # drafts offered to the user; nothing written
    answer: str
