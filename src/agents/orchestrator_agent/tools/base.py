"""What a tool is, and what it gets told about the run calling it.

Separate from `registry.py` so a tool module can import `Tool` without importing the
registry that collects it — otherwise every adapter and the registry would import each
other.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from pydantic import BaseModel

from src.common.schemas.context import Context
from src.common.schemas.idea import IdeaRecord
from src.agents.orchestrator_agent.schemas import ToolResult


@dataclass(frozen=True)
class ToolEnv:
    """Which run is calling, and what it may spend.

    `max_llm_calls` is this step's slice of the turn's budget. A subagent's own ceiling is
    per call, which is the right unit for it and the wrong one for a turn: several steps
    across several reflect rounds multiply. Zero means "use the subagent's own default".

    `chained` is what the steps this one declared in `needs` produced, by step number. It
    is here rather than folded into the context because a `step#position` reference has to
    resolve to *one* record, and the flattened context has lost which step made what.
    """

    category_slug: str = ""
    session_id: str = ""
    run_id: str = ""
    max_llm_calls: int = 0
    chained: dict[int, list[IdeaRecord]] = field(default_factory=dict)


@dataclass(frozen=True)
class Tool:
    """One thing the orchestrator can do.

    `description` says what the tool is *for*; how to call it comes from `args_schema`,
    whose field types and descriptions `catalog()` renders for the planner. Keep the two
    from overlapping or they drift.

    `args_schema` is the planner-facing contract, which is deliberately narrower than the
    subagent's own call schema: arguments the planner must not choose (assembled context,
    compute budgets) are left out and filled in by the adapter.
    """

    name: str
    description: str
    args_schema: type[BaseModel]
    fn: Callable[[BaseModel, Context, ToolEnv], ToolResult]
