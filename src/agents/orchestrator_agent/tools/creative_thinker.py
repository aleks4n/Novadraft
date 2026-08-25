"""The creative thinker as the orchestrator calls it.

Owns two things: what the planner may fill in (`ThinkerArgs`), and how those become the
subagent's real call (`ThinkerCall` in `src/agents/creative_thinker/schemas.py`).

The thinker is pure — it deep-copies the idea it is handed, mutates the copy, and returns
an `Envelope`. Nothing here writes; persisting an accepted result is
`idea_agent.apply_envelope`, and only after the user decides.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from src.agents.creative_thinker.schemas import AgentStyle, Budget, Envelope, ThinkerCall
from src.agents.creative_thinker.styles import ROSTER, DEFAULT_REFINEMENT_STYLE
from src.common.schemas.context import Context
from src.common.schemas.idea import IdeaRecord
from src.agents.orchestrator_agent.schemas import ToolResult
from src.agents.orchestrator_agent.tools.base import Tool, ToolEnv

ThinkingMode = Literal["generation", "refinement"]

# The roster is a closed set on purpose. A style is a prompt file under
# `creative_thinker/styles/`, and `load_style_prompt` falls back to `neutral` for a name it
# cannot find — deliberately, so an unwritten style still runs. The consequence for a
# planner writing free prose is that every lens misses, every candidate comes from the same
# neutral stance, and nothing raises. As a Literal the allowed values reach the planner
# through `catalog()` and a name outside them fails at dispatch instead.
Style = Literal[
    "optimizer", "combiner", "skeptic", "transferer", "minimalist", "extremist", "neutral"
]

_MAX_WIDTH = len(ROSTER)  # six distinct stances; beyond that the fan-out repeats one


class ThinkerArgs(BaseModel):
    """What the *planner* supplies — a deliberate subset of `ThinkerCall`.

    `context` and `idea` are absent because they are assembled deterministically from what
    the session holds; the planner decides what to do, never what is in scope. `work_on`
    is how it says *which* idea, by an id it can see in its own context.

    `budget` is absent because a per-turn ceiling is the orchestrator's to divide, not a
    number a plan may raise.
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
    styles: list[Style] = Field(
        default_factory=list,
        description="Stances to think from, one per candidate in generation mode. "
                    "Diversity comes from these, so pick genuinely different ones.",
    )
    work_on: list[str] = Field(
        default_factory=list,
        description="Ids of the ideas already in your context that this call should work "
                    "on — use it when several are in scope and the user meant one of them. "
                    "Only ids listed in the context you were given. Leave empty to work on "
                    "everything in scope.",
    )
    width: int = Field(
        default=3,
        ge=1,
        le=_MAX_WIDTH,
        description=f"How many candidates a generation call returns, between 1 and "
                    f"{_MAX_WIDTH}. Set it to the number the user asked for; leave the "
                    f"default when they gave none. Ignored in refinement mode.",
    )
    depth: int = Field(
        default=3,
        ge=1,
        le=4,
        description="Refinement only: how many levels the tree goes. 1 is a single pass "
                    "and loses most of what refinement is for — raise it when the user "
                    "wants an idea thought through rather than just touched up.",
    )
    tool_use: bool = Field(
        default=False,
        description="Let the thinker research mid-thought: slower, better grounded.",
    )


def _subject(args: ThinkerArgs, ctx: Context) -> tuple[IdeaRecord, Context, str]:
    """The idea this call works on, the context around it, and a reason to fail.

    The thinker takes the subject as its own argument and treats `context.ideas` as
    *related* ideas — which is what makes "refinement deepens ONE existing idea" true.
    `work_on` picks that subject out of what the planner could see; an id that is not
    actually in context fails the step with a reason rather than silently working
    whichever idea happened to come first.
    """
    ideas = list(ctx.ideas)
    if args.work_on:
        wanted = set(args.work_on)
        chosen = [idea for idea in ideas if idea.idea_id in wanted]
        if not chosen:
            return IdeaRecord(), ctx, f"work_on named {args.work_on}, none of which is in context"
        subject = chosen[0]
    elif args.thinking_mode == "refinement":
        if not ideas:
            return IdeaRecord(), ctx, "refinement needs an idea in context and none is"
        subject = ideas[0]
    else:
        # Generation has no subject; every idea in scope is background it is deduplicated
        # against.
        return IdeaRecord(), ctx, ""

    rest = [idea for idea in ideas if idea is not subject]
    return subject, ctx.model_copy(update={"ideas": rest}), ""


def _styles(args: ThinkerArgs) -> list[AgentStyle]:
    """Named stances into the thinker's own style objects.

    Refinement runs one stance, generation fans out over several; `pick_styles` tops the
    list up from the roster when the planner named fewer than `width`.
    """
    if args.styles:
        return [AgentStyle(name=name) for name in args.styles]
    if args.thinking_mode == "refinement":
        return [AgentStyle(name=DEFAULT_REFINEMENT_STYLE)]
    return []


def run(args: ThinkerArgs, ctx: Context, env: ToolEnv) -> ToolResult:
    """Planner args + assembled context -> the subagent's typed call.

    `status` and `limit` are carried back out. A run that hit a ceiling returns the last
    completed round's survivors labelled `truncated`, and reflect has to be able to tell
    that apart from thinking that simply went badly.
    """
    subject, around, problem = _subject(args, ctx)
    if problem:
        return ToolResult(tool=TOOL.name, ok=False, error=problem)

    call = ThinkerCall(
        mode=args.thinking_mode,
        idea=subject,
        context=around.model_dump(mode="json"),
        task=args.instruction,
        styles=_styles(args),
        width=args.width,
        depth=args.depth,
        tool_use=args.tool_use,
        budget=Budget(max_llm_calls=env.max_llm_calls) if env.max_llm_calls else Budget(),
        session_id=env.session_id or None,
        run_id=env.run_id or None,
    )

    from src.agents.creative_thinker import think

    envelope: Envelope = think(call)
    # A refinement offers the modified copy of its subject; a generation offers new
    # candidates, and its `envelope.idea` is a titleless carrier holding the run's thought
    # graph rather than something to show. Either way the whole envelope is carried out:
    # the trace lives on that carrier, and the accept step needs it to attach the right
    # part of it to whichever candidate the user takes.
    ideas = list(envelope.drafts)
    if args.thinking_mode == "refinement":
        ideas.insert(0, envelope.idea)

    return ToolResult(
        tool=TOOL.name,
        ideas=ideas,
        text=envelope.answer,
        status=envelope.status,
        limit=envelope.limit or "",
        envelope=envelope.model_dump(mode="json"),
    )


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
