"""What a tool is, and what it gets told about the run calling it.

Separate from `registry.py` so a tool module can import `Tool` without importing the
registry that collects it — otherwise every adapter and the registry would import each
other.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from pydantic import BaseModel

from src.common.schemas.context import Context
from src.agents.orchestrator_agent.schemas import ToolResult


@dataclass(frozen=True)
class ToolEnv:
    """Which run is calling. Tools need it for provenance and session-scoped writes."""

    category_slug: str = ""
    session_id: str = ""
    run_id: str = ""


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
