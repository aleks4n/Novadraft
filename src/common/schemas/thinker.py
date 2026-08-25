"""The creative thinker's call contract — shared between the orchestrator and the thinker.

Lives in `common/` because it is the interface between two separately-owned agents: the
orchestrator fills these slots, the creative thinker implements everything behind them.
Changing this file is a cross-team change.

    thinking_mode   generation (fan-out) | refinement (tree / chain of thoughts)
    context         what the orchestrator loaded, plus the instruction it wrote
    lenses          personality presets, prepended to the thinker's prompt
    guard           width and depth of the thought tree — fixed for now
    tool_use        may the thinker call the investigator itself?
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

from src.common.schemas.context import Context
from src.common.schemas.idea import Idea

ThinkingMode = Literal["generation", "refinement"]


class Guard(BaseModel):
    """Bounds on the thought tree — the thinker's only cost control.

    Fixed for now; the orchestrator does not vary them. Generation fans out (width =
    candidates, depth 1); refinement goes deep (width = beam per level, depth = levels),
    and width 1 depth 1 is a plain chain of thought through the same code path.
    """

    width: int = 5
    depth: int = 1


class CreativeThinkerCall(BaseModel):
    """The full call, built by the orchestrator's execute step."""

    thinking_mode: ThinkingMode
    context: Context
    instruction: str = Field(
        default="",
        description="The task for this call, in the orchestrator's words. Not the raw user text.",
    )
    lenses: list[str] = Field(
        default_factory=list,
        description=(
            "Personality / way-of-thinking presets, one per parallel branch in generation. "
            "Each string is prepended to the thinker's prompt."
        ),
    )
    guard: Guard = Field(default_factory=Guard)
    tool_use: bool = False


class CreativeThinkerResult(BaseModel):
    """What comes back: the ideas it worked on, and what it wants to say about them.

    In generation these are new drafts; in refinement it is the idea it was given, with
    fields rewritten and its thought graph appended. Either way they are `status="draft"`
    until the user accepts them — the thinker never writes to the idea graph.
    """

    ideas: list[Idea] = Field(default_factory=list)
    text: str = Field(
        default="",
        description="The thinker's own account: what it explored, what it is unsure about.",
    )
    escalation: Optional[str] = Field(
        default=None,
        description="A question only the user can settle, e.g. 'this branch is its own idea'.",
    )
