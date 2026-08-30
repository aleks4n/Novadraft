"""The creative thinker as the orchestrator calls it.

Owns two things: what the planner may fill in (`ThinkerArgs`), and how those become the
subagent's real call (`ThinkerCall` in `src/agents/creative_thinker/schemas.py`).

The thinker is pure — it deep-copies the idea it is handed, mutates the copy, and returns
an `Envelope`. Nothing here writes; persisting an accepted result is
`idea_agent.apply_envelope`, and only after the user decides.
"""

from __future__ import annotations

from typing import Callable, Literal

from pydantic import BaseModel, Field

from src.agents.creative_thinker.schemas import AgentStyle, Budget, Envelope, ThinkerCall
from src.agents.creative_thinker.styles import ROSTER, DEFAULT_REFINEMENT_STYLE
from src.agents.creative_thinker.tools.context import render_context as render_thinker_context
from src.common.schemas.context import Context
from src.common.schemas.idea import IdeaRecord
from src.common.utils.logging import trace
from src.agents.orchestrator_agent.schemas import ToolResult, parse_step_ref, step_ref
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

    `context` and `idea` are absent because they are assembled from what the session holds;
    the planner cannot edit that bundle directly. `work_on` is how it says *which* idea, by
    an id it can see in its own context.

    That is a narrower rule than it used to be. The planner *can* widen what a later step
    sees — by planning an investigator step that fetches a record and declaring it in
    `needs`, which folds the answer in as a finding. What it still cannot do is reach into
    the workspace silently: every widening is a step on the plan, approved at the plan gate
    and visible in the run. Auditable, rather than fixed.

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
        description="Which idea this call works on. Either an idea id from the context you "
                    "were given, or the output of an earlier step of this plan written as "
                    "step#position, both counted from 1 — 1#2 is the second idea step 1 "
                    "returns. Use the step form when the idea does not exist yet because "
                    "an earlier step is about to make it. Leave empty to work on whatever "
                    "is in scope.",
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


def _from_earlier_step(ref: str, env: ToolEnv) -> IdeaRecord | None:
    """A `step#position` reference resolved against what that step actually returned."""
    parsed = parse_step_ref(ref)
    if parsed is None:
        return None
    step, position = parsed
    produced = env.chained.get(step) or []
    return produced[position - 1] if 1 <= position <= len(produced) else None


def _subject(args: ThinkerArgs, ctx: Context,
             env: ToolEnv) -> tuple[IdeaRecord, Context, str, str]:
    """The idea this call works on, the context around it, a reason to fail, and the
    handle of what it consumed.

    The thinker takes the subject as its own argument and treats `context.ideas` as
    *related* ideas — which is what makes "refinement deepens ONE existing idea" true.
    `work_on` picks that subject out of what the planner could see; a name that resolves
    to nothing fails the step with a reason rather than silently working whichever idea
    happened to come first.

    Two kinds of name resolve. An `idea_id` names a record in the workspace. A
    `step#position` handle names one output of an earlier step in this same plan — which
    is the only way to say "the second of the three you are about to generate", since
    those have no ids until someone accepts them. The fourth return value is that handle
    when one was used, so the caller can stop offering the record this call rewrote.
    """
    ideas = list(ctx.ideas)
    if args.work_on:
        by_id = {idea.idea_id: idea for idea in ideas if idea.idea_id}
        chosen = [found for found in
                  (_from_earlier_step(ref, env) or by_id.get(ref) for ref in args.work_on)
                  if found is not None]
        if not chosen:
            return (IdeaRecord(), ctx,
                    f"work_on named {args.work_on}, none of which is in context", "")
        subject = chosen[0]
    elif args.thinking_mode == "refinement":
        if not ideas:
            return IdeaRecord(), ctx, "refinement needs an idea in context and none is", ""
        subject = ideas[0]
    else:
        # Generation has no subject; every idea in scope is background it is deduplicated
        # against.
        return IdeaRecord(), ctx, "", ""

    rest = [idea for idea in ideas if idea is not subject]
    return subject, ctx.model_copy(update={"ideas": rest}), "", _handle_for(subject, env)


def _handle_for(subject: IdeaRecord, env: ToolEnv) -> str:
    """The `step#position` handle of the subject, when it came from an earlier step.

    Derived from the record rather than from whatever `work_on` said, so a refinement that
    named nothing and simply took the first idea reports its consumption too — that is the
    plain `generate -> refine` case, and the one that put the same idea on screen twice.

    Compared by identity: `_context_for` passes the very objects the earlier step returned,
    so this asks "is this that record" rather than "does this look like it".
    """
    for step, produced in env.chained.items():
        for position, candidate in enumerate(produced, 1):
            if candidate is subject:
                return step_ref(step, position)
    return ""


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


def _investigator(env: ToolEnv) -> Callable[[str], dict]:
    """The investigator as the *thinker* asks it: a question in, an answer out.

    This is the half of `tool_use` that lives on this side. The thinker takes an
    investigator as an argument and skips every lookup when it is absent — which it always
    was, because this adapter called `think(call)` and passed nothing. A plan could ask for
    grounded thinking and quietly get ungrounded thinking.

    Scoped to the session on purpose. The investigator saves what it downloads, and the
    thinker's own factory hardcodes a harness folder; passing the session through is what
    puts a paper the thinker asked for where the next turn will look for it.

    The question goes through unchanged. The investigator routes free text to the paper
    pipeline or to the web itself, and it is the component that knows which a question
    deserves — the harness prefixes "search the web:" to force the cheap path, which is a
    decision about its own demo rather than about what the thinker asked.
    """

    def ask(question: str) -> dict:
        # Imported on first use: the investigator pulls in the paper pipeline and the web
        # stack, and a run that never asks anything should not pay for that.
        from src.agents.investigator_agent import run as run_investigator

        answer = run_investigator(question,
                                  session_id=env.session_id or "session-adhoc",
                                  category_slug=env.category_slug or None,
                                  stream=False)
        # `sources` is the thinker's field for citations. The investigator reports its
        # findings as prose and exposes no structured source list, so this stays empty
        # rather than being invented from the text.
        return {"answer": str(answer or ""), "sources": []}

    return ask


def _trace_handover(call: ThinkerCall, subject: IdeaRecord, around: Context) -> None:
    """Record what this step is handing the thinker, before it runs.

    Nothing logged what the thinker could see. The step's own log line carries the tool
    name and the planner's `why`, and the UI's plan row shows the planner's arguments —
    all of which describe what was *asked for*, none of which describes what was *in
    scope*. So the one question a disappointing run raises first — "did it even have the
    paper?" — could not be answered from the run at all, only by re-deriving the assembly
    by hand.

    Emitted rather than returned because it is evidence about the run, not an input to it:
    it lands in the same `.jsonl` the agent streams, so a replayed run shows the same
    handover a live one did.

    The rendered markdown goes in whole. It is the literal text the thinker's prompt
    carries, and a count of records is not a substitute — "1 gap, 1 paper" reads as
    sufficient context right up until you see that the paper block was one truncated line.
    """
    inventory = {
        "gaps": [g.gap_id for g in around.gaps],
        "papers": [str(p.get("paper_id", "?")) for p in around.papers],
        "related_ideas": [i.idea_id or "(draft)" for i in around.ideas],
        "user_input": [u.title or u.id for u in around.user_input],
        "findings": len(around.findings),
        "missing_ids": list(around.missing_ids),
        "catalog_counts": (around.catalog or {}).get("counts") or {},
    }
    rendered = render_thinker_context(call.context)
    trace.emit({
        "type": "event",
        "name": "context handed to creative thinker",
        "data": {
            "mode": call.mode,
            "task": call.task,
            "subject_idea": subject.idea_id or ("(new — generation)" if not subject.title
                                                else "(draft)"),
            "styles": [s.name for s in call.styles],
            "width": call.width,
            "depth": call.depth,
            "tool_use": call.tool_use,
            "inventory": inventory,
            "context_chars": len(rendered),
            "context_md": rendered,
        },
    })


def run(args: ThinkerArgs, ctx: Context, env: ToolEnv) -> ToolResult:
    """Planner args + assembled context -> the subagent's typed call.

    `status` and `limit` are carried back out. A run that hit a ceiling returns the last
    completed round's survivors labelled `truncated`, and reflect has to be able to tell
    that apart from thinking that simply went badly.
    """
    subject, around, problem, consumed = _subject(args, ctx, env)
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

    _trace_handover(call, subject, around)

    from src.agents.creative_thinker import think

    # NOTE: an investigation is charged to the thinker's own `max_investigations` counter,
    # not to `metrics.llm_calls` — and `llm_calls` is the only thing `execute` reads when
    # it charges a step. So a `tool_use` run can spend several full investigator runs that
    # the turn budget never sees. Deliberately left for the budget pass.
    envelope: Envelope = think(
        call, investigator=_investigator(env) if args.tool_use else None)
    # A refinement offers the modified copy of its subject; a generation offers new
    # candidates, and its `envelope.idea` is a titleless carrier holding the run's thought
    # graph rather than something to show. Either way the whole envelope is carried out:
    # the trace lives on that carrier, and the accept step needs it to attach the right
    # part of it to whichever candidate the user takes.
    ideas = list(envelope.drafts)
    draft_index: list[int | None] = list(range(len(envelope.drafts)))
    if args.thinking_mode == "refinement":
        ideas.insert(0, envelope.idea)
        draft_index.insert(0, None)

    return ToolResult(
        tool=TOOL.name,
        ideas=ideas,
        draft_index=draft_index,
        text=envelope.answer,
        status=envelope.status,
        limit=envelope.limit or "",
        envelope=envelope.model_dump(mode="json"),
        consumed=consumed,
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
