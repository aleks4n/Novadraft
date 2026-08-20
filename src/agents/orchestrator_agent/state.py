"""The orchestrator graph's state — what flows between its nodes.

A LangGraph TypedDict; `total=False` so each node returns only the keys it sets.

    assemble_context -> plan -> execute -> reflect -> finish
                         | ^        |                  ^
                         | +--------+  (replan/cont.)  |
                         +-- plan called no tools -----+

Accepting an idea is *not* part of this graph — it needs no LLM, so it is a plain
function call (`agent.commit_selection`).
"""

from __future__ import annotations

from typing import List, TypedDict

from src.common.schemas.context import Context
from src.common.schemas.idea import Idea
from src.agents.orchestrator_agent.schemas import (
    FocusSelection,
    Plan,
    ReflectJudgment,
    ToolResult,
)


MAX_ITERATIONS = 3
"""Reflect loops before `finish` is forced. Lives here rather than in `agent.py` because
reflect is told which round it is on, and importing the graph module would be circular."""


class OrchestratorState(TypedDict, total=False):
    # --- inputs, set at the boundary (run()) ---
    session_id: str
    category_slug: str
    run_id: str  # this turn; names the log file, the run result, and each idea's origin
    message: str  # the user's text
    focus: FocusSelection  # gap_ids + idea_ids clicked in the UI

    # --- after assemble_context (deterministic) ---
    context: Context
    history: List[dict]  # earlier turns of this session: {message, answer}, oldest first

    # --- after plan (LLM) ---
    plan: Plan

    # --- after execute (deterministic dispatch) ---
    results: List[ToolResult]
    carried: int  # how many of `results` predate this plan, so they aren't paired by index

    # --- after reflect (LLM) ---
    judgment: ReflectJudgment
    iteration: int  # incremented in reflect; the loop guard

    # --- after finish ---
    ideas: List[Idea]  # drafts offered to the user; nothing written
    answer: str
