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
    needs: list[int] = Field(
        default_factory=list,
        description="1-based numbers of earlier steps whose output this step works on. "
                    "Steps otherwise see only what the user selected, so a step that "
                    "refines what an earlier step produced must say so here.",
    )


class Plan(BaseModel):
    """The planner's output — shown to the user before it runs.

    Empty `steps` is a real plan, not a failed one: the turn was conversational and
    `answer` holds the reply. The planner writes that reply rather than reflect, because
    on such a turn there are no results to reflect on and the planner is the only step
    that sees the tool catalogue — so it is the only one that can answer "what can you
    do?" from the actual tools rather than from a description of them.
    """

    goal: str = Field(default="", description="The user's request restated in one line.")
    steps: list[ToolCall] = Field(default_factory=list)
    done_criteria: list[str] = Field(
        default_factory=list,
        description="Checkable conditions. Reflect has nothing to judge without these.",
    )
    answer: str = Field(
        default="",
        description="Your reply to the user, when the turn needs no tools. Fill this "
                    "only when `steps` is empty; otherwise leave it blank and the "
                    "reflection step writes the reply once the results are in.",
    )


# --- reflect (LLM output) ----------------------------------------------------

Route = Literal["finish", "replan"]


class ReflectJudgment(BaseModel):
    """Whether the plan's goal is met, and what to do next.

    A shortfall goes back to the planner rather than re-running the same steps: it costs
    the same single LLM call, and it says *what* to do differently instead of hoping the
    same call returns something better.

    Defaults to `finish` so an offline run (empty mock output) terminates instead of
    looping to the iteration cap.
    """

    route: Route = "finish"
    keep_results: bool = Field(
        default=True,
        description="On `replan`: keep what the tools already returned and let the new "
                    "plan add to it. False throws this round away and starts clean — for "
                    "when the approach was wrong, not merely incomplete.",
    )
    feedback: str = Field(
        default="",
        description="On `replan`, what the planner must do differently — specific enough "
                    "that it does not repeat the mistake. On `finish`, why the criteria "
                    "are met.",
    )
    answer: str = Field(default="", description="The message shown to the user.")


# --- execute -----------------------------------------------------------------


class ToolResult(BaseModel):
    """One dispatched step's outcome. A failure is recorded, not raised."""

    tool: str
    ok: bool = True
    error: str = ""
    ideas: list[Idea] = Field(default_factory=list)
    text: str = ""
