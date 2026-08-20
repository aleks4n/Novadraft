"""The creative thinker as the orchestrator calls it.

Owns two things: what the planner may fill in (`ThinkerArgs`), and how those become the
subagent's real call (`CreativeThinkerCall`, the cross-team contract in
`common/schemas/thinker.py`).

**Integration point.** `think` is currently the deterministic stand-in in
`dummy_thinker.py`. When the real agent lands, repoint that import — nothing else here
changes, because the contract it must implement is already what this builds.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from src.common.schemas.context import Context
from src.common.schemas.thinker import (
    CreativeThinkerCall,
    CreativeThinkerResult,
    Guard,
    ThinkingMode,
)
from src.agents.orchestrator_agent.schemas import ToolResult
from src.agents.orchestrator_agent.tools.base import Tool, ToolEnv
from src.agents.orchestrator_agent.tools.dummy_thinker import think


class ThinkerArgs(BaseModel):
    """What the *planner* supplies — a deliberate subset of `CreativeThinkerCall`.

    `context` is absent because it is assembled deterministically from what the user
    selected; the planner decides what to do, never what is in scope.

    `guard` is not exposed whole: `width` is here because "give me 3 ideas" is a number
    the user states out loud and the planner is the only step that reads it, but it is
    bounded so a plan still cannot spend more than we intend. `depth` stays fixed — no
    user phrasing maps to it, and it multiplies cost.

    `thinking_mode` is a Literal rather than a bare str: the two valid values then reach
    the planner through `catalog()` and are enforced at dispatch. As a plain string the
    model had nothing to go on and passed a boolean.
    """

    thinking_mode: ThinkingMode = Field(
        default="generation",
        description="'generation' fans out into new candidates; 'refinement' deepens ONE "
                    "existing idea that is already in context.",
    )
    instruction: str = Field(
        default="",
        description="The task for this call, in your own words — restate anything from "
                    "the user's message that should steer it.",
    )
    lenses: list[str] = Field(
        default_factory=list,
        description="Personality presets, one per candidate in generation mode (e.g. "
                    "'a skeptic who questions the premise'). Diversity comes from these.",
    )
    width: int = Field(
        default=Guard().width,
        ge=1,
        le=8,
        description="How many candidates a generation call returns, between 1 and 8. Set "
                    "it to the number the user asked for; leave the default when they "
                    "gave none. Ignored in refinement mode.",
    )
    tool_use: bool = Field(
        default=False,
        description="Let the thinker research mid-thought: slower, better grounded.",
    )


def run(args: ThinkerArgs, ctx: Context, _env: ToolEnv) -> ToolResult:
    """Planner args + assembled context -> the subagent's typed call."""
    call = CreativeThinkerCall(
        thinking_mode=args.thinking_mode,
        context=ctx,
        instruction=args.instruction,
        lenses=args.lenses,
        guard=Guard(width=args.width),  # depth stays at its default
        tool_use=args.tool_use,
    )
    result: CreativeThinkerResult = think(call)
    return ToolResult(tool=TOOL.name, ideas=result.ideas, text=result.text)


TOOL = Tool(
    name="creative_thinker",
    # What the tool is FOR. How to call it is carried by the arg descriptions above,
    # which catalog() renders — don't restate them here or the two will drift.
    description=(
        "Thinks about ideas, and returns drafts — it never writes to the idea graph. "
        "Use it when the user wants new ideas for a gap, or an existing idea improved, "
        "pushed on, or questioned."
    ),
    args_schema=ThinkerArgs,
    fn=run,
)
