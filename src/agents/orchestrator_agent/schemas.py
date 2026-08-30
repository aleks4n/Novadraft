"""Orchestrator schemas — its own I/O and the LLM output shapes.

Every field carries a default so `model_construct()` (what the offline `_MockChat`
returns when no API key is set) yields a usable object and a dev run still terminates.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field

from src.common.schemas.idea import IdeaRecord

# --- addressing a draft that does not exist yet -------------------------------

_STEP_REF = re.compile(r"^\s*(\d+)\s*#\s*(\d+)\s*$")


def step_ref(step: int, position: int) -> str:
    """The handle for one output of one step, both counted from 1.

    Ideas made during a turn have no `idea_id` — nothing has accepted them — so there is
    otherwise no way to say *which* of three candidates something means. A plan can name
    one before it exists, because the plan is what decides how many the step returns.

    The same string is used to point at a draft, to report which one a step consumed, and
    to drop the consumed one from what is offered. One vocabulary, so the three cannot
    disagree about what "the second candidate" is.
    """
    return f"{step}#{position}"


def parse_step_ref(ref: str) -> tuple[int, int] | None:
    """`"1#2"` -> `(1, 2)`, or None if it is not a step reference (an `idea_id` is not)."""
    match = _STEP_REF.match(str(ref))
    return (int(match.group(1)), int(match.group(2))) if match else None


# --- session I/O -------------------------------------------------------------


class FocusSelection(BaseModel):
    """What the user clicked in the UI panels — the session's scope."""

    gap_ids: list[str] = Field(default_factory=list)
    idea_ids: list[str] = Field(default_factory=list)


class OrchestratorInput(BaseModel):
    """One user turn.

    `require_approval` stops the turn once it has a plan that calls tools and offers that
    plan instead of running it. `approved_plan` is the other half: the plan the user
    started, as they left it, which the run adopts instead of planning again. A caller
    sets one or the other, never both — they are the two halves of one turn.
    """

    session_id: str
    category_slug: str
    message: str = ""
    focus: FocusSelection = Field(default_factory=FocusSelection)
    require_approval: bool = False
    approved_plan: "Plan | None" = None


class OrchestratorOutput(BaseModel):
    """What the chat renders. `ideas` are drafts on offer — nothing is written yet.

    `status` says which kind of offer this is: `completed` offers the ideas, and
    `awaiting_approval` offers `plan`, with nothing run yet.
    """

    run_id: str
    answer: str = ""
    status: str = "completed"
    plan: "Plan" = Field(default_factory=lambda: Plan())
    ideas: list[IdeaRecord] = Field(default_factory=list)


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
    """One dispatched step's outcome. A failure is recorded, not raised.

    `status` and `limit` come from the subagent when it has them. A run that hits a
    compute ceiling returns the survivors of its last completed round and says so — that
    is the normal way divergent thinking ends, not an error — and reflect has to be able
    to tell it apart from thinking that simply went badly, or it replans into a budget
    that is already spent.
    """

    tool: str
    ok: bool = True
    error: str = ""
    ideas: list[IdeaRecord] = Field(default_factory=list)
    text: str = ""
    status: str = "completed"
    limit: str = ""
    draft_index: list[int | None] = Field(
        default_factory=list,
        description="Parallel to `ideas`: where each one sits in `envelope`. An integer "
                    "indexes `envelope.drafts`; None means it *is* the envelope's own "
                    "record. Reported by the adapter, which knows, rather than inferred "
                    "later from whether an id is present — a refinement of a draft "
                    "rewrites a record that has no id either, and guessing gets it wrong.",
    )
    consumed: str = Field(
        default="",
        description="The `step#position` handle of the earlier draft this step took as "
                    "its subject, if it took one. A refinement returns a rewrite of what "
                    "it was given, so the original stops being a separate thing to offer "
                    "— without this the user is shown the same idea twice, before and "
                    "after, with nothing saying which supersedes which.",
    )
    envelope: dict[str, Any] = Field(
        default_factory=dict,
        description="The subagent's own return, kept whole and opaque. A generation run's "
                    "thinking graph lives on the envelope's carrier record rather than on "
                    "the candidates, and the accept step needs the real envelope to carve "
                    "the right slice of it onto whichever draft the user takes.",
    )


OrchestratorInput.model_rebuild()
OrchestratorOutput.model_rebuild()
