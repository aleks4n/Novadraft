"""Orchestrator schemas — its own I/O and the LLM output shapes.

Every field carries a default so `model_construct()` (what the offline `_MockChat`
returns when no API key is set) yields a usable object and a dev run still terminates.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from src.common.schemas.idea import Idea

# --- session I/O -------------------------------------------------------------


class FocusSelection(BaseModel):
    """What the user clicked in the UI panels — the session's scope."""

    gap_ids: list[str] = Field(default_factory=list)
    idea_ids: list[str] = Field(default_factory=list)


class OrchestratorInput(BaseModel):
    """One user turn."""

    session_id: str
    category_slug: str
    message: str = ""
    focus: FocusSelection = Field(default_factory=FocusSelection)


class OrchestratorOutput(BaseModel):
    """What the chat renders. `ideas` are drafts on offer — nothing is written yet."""

    run_id: str
    answer: str = ""
    ideas: list[Idea] = Field(default_factory=list)


# --- plan (LLM output) -------------------------------------------------------


class ToolCall(BaseModel):
    """One step of the plan.

    `args` is a free dict rather than a per-tool model: the orchestrator is deliberately
    unconstrained in *how* it composes tools, and each tool validates its own args
    against a real schema at dispatch time (see tools/registry.py).
    """

    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    why: str = ""


class Plan(BaseModel):
    """The planner's output — shown to the user before it runs."""

    goal: str = Field(default="", description="The user's request restated in one line.")
    steps: list[ToolCall] = Field(default_factory=list)
    done_criteria: list[str] = Field(
        default_factory=list,
        description="Checkable conditions. Reflect has nothing to judge without these.",
    )


# --- reflect (LLM output) ----------------------------------------------------

Route = Literal["continue", "replan", "finish"]


class ReflectJudgment(BaseModel):
    """Whether the plan's goal is met, and what to do next.

    Defaults to `finish` so an offline run (empty mock output) terminates instead of
    looping to the iteration cap.
    """

    route: Route = "finish"
    reasoning: str = ""
    answer: str = Field(default="", description="The message shown to the user.")


# --- execute -----------------------------------------------------------------


class ToolResult(BaseModel):
    """One dispatched step's outcome. A failure is recorded, not raised."""

    tool: str
    ok: bool = True
    error: str = ""
    ideas: list[Idea] = Field(default_factory=list)
    text: str = ""
