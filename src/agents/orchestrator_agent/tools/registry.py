"""The tool registry — what the orchestrator can do, and how the planner learns about it.

The orchestrator is deliberately *not* constrained to a fixed set of operations: it is
given a goal and a catalogue of tools and composes them itself. That makes these
descriptions the real specification — `catalog()` renders them straight into the planner's
prompt, so a change here changes what the orchestrator knows how to do.

Both tool bodies are stubs. The wiring around them is real: args are validated, the typed
subagent call is built, failures are captured rather than raised.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from pydantic import BaseModel, Field, ValidationError

from src.common.schemas.context import Context
from src.common.schemas.thinker import (
    CreativeThinkerCall,
    CreativeThinkerResult,
    Guard,
)
from src.agents.orchestrator_agent.schemas import ToolCall, ToolResult
from src.agents.orchestrator_agent.tools.dummy_thinker import think


@dataclass(frozen=True)
class Tool:
    name: str
    description: str  # what the planner sees — this is the spec
    args_schema: type[BaseModel]
    fn: Callable[[BaseModel, Context, "ToolEnv"], ToolResult]


@dataclass(frozen=True)
class ToolEnv:
    """Which run is calling. Tools need it for provenance and session-scoped writes."""

    category_slug: str = ""
    session_id: str = ""
    run_id: str = ""


# --- creative thinker --------------------------------------------------------


class ThinkerArgs(BaseModel):
    """What the planner supplies. `guard` is deliberately absent — width and depth are
    fixed for now, so the planner cannot spend more compute than we intend."""

    thinking_mode: str = Field(
        default="generation", description="'generation' to fan out, 'refinement' to go deep."
    )
    instruction: str = Field(
        default="", description="The task for this call, in the orchestrator's own words."
    )
    lenses: list[str] = Field(default_factory=list)
    tool_use: bool = False


def _run_creative_thinker(args: ThinkerArgs, ctx: Context, _env: ToolEnv) -> ToolResult:
    """Builds the typed call and hands it to the thinker.

    `think` is currently the deterministic stand-in in `dummy_thinker.py`. When the real
    agent lands, repoint this import — the contract is already what it must implement.
    """
    call = CreativeThinkerCall(
        thinking_mode="refinement" if args.thinking_mode == "refinement" else "generation",
        context=ctx,
        instruction=args.instruction,
        lenses=args.lenses,
        guard=Guard(),  # fixed for now
        tool_use=args.tool_use,
    )
    result: CreativeThinkerResult = think(call)
    return ToolResult(tool="creative_thinker", ideas=result.ideas, text=result.text)


# --- investigator ------------------------------------------------------------


class InvestigatorArgs(BaseModel):
    query: str = Field(default="", description="What to find out, as a plain question.")


def _run_investigator(args: InvestigatorArgs, _ctx: Context, env: ToolEnv) -> ToolResult:
    """Calls the real investigator agent (src/agents/investigator_agent), which returns
    plain text.

    Imported lazily: it pulls in the paper agent and the web stack, and the registry is
    imported by the dev harness at discovery time.

    KNOWN MISMATCH: the investigator writes its downloads to `workspace/<session_id>/`,
    treating the session id as a category (it even writes a meta.json there, so the
    session shows up in /api/topics as a research topic). This orchestrator keeps session
    files under `workspace/<category>/sessions/<session_id>/`. Until that is reconciled,
    only the returned text is used — fetched papers won't appear in the next turn's
    context.
    """
    if not args.query.strip():
        return ToolResult(tool="investigator", ok=False, error="empty query")
    from src.agents.investigator_agent import run as run_investigator

    text = run_investigator(args.query, session_id=env.session_id or "session-adhoc",
                            stream=False)
    return ToolResult(tool="investigator", text=str(text))


# --- registry ----------------------------------------------------------------

TOOLS: dict[str, Tool] = {
    "creative_thinker": Tool(
        name="creative_thinker",
        description=(
            "Thinks about ideas. Two modes. 'generation' fans out: runs one agent style "
            "per lens in parallel over the context and returns a rough candidate idea from "
            "each — use it when the user wants new ideas for a gap. 'refinement' goes deep: "
            "a tree of thoughts on ONE existing idea, returning a sharpened version of it — "
            "use it when the user wants an idea improved, pushed on, or questioned. It "
            "never writes to the idea graph; it returns drafts. Set tool_use=true to let it "
            "research mid-thought (slower, better grounded)."
        ),
        args_schema=ThinkerArgs,
        fn=_run_creative_thinker,
    ),
    "investigator": Tool(
        name="investigator",
        description=(
            "Researches a question against real sources (literature, the workspace) and "
            "reports what it found, saving any papers it fetches into the session. Use it "
            "when the user asks to look something up, or when the answer needs evidence "
            "rather than thinking. It does not produce ideas."
        ),
        args_schema=InvestigatorArgs,
        fn=_run_investigator,
    ),
}


def catalog() -> str:
    """The tool list as the planner sees it — rendered into subagents/plan/prompt.md."""
    lines = []
    for tool in TOOLS.values():
        fields = ", ".join(tool.args_schema.model_fields)
        lines.append(f"- {tool.name}({fields})\n    {tool.description}")
    return "\n".join(lines)


def dispatch(call: ToolCall, ctx: Context, env: ToolEnv) -> ToolResult:
    """Run one planned step. Unknown tools and bad args are results, not exceptions — the
    orchestrator should be able to reflect on a failed step and replan."""
    tool = TOOLS.get(call.tool)
    if tool is None:
        return ToolResult(tool=call.tool, ok=False, error=f"unknown tool '{call.tool}'")
    try:
        args = tool.args_schema.model_validate(call.args)
    except ValidationError as exc:
        return ToolResult(tool=call.tool, ok=False, error=f"bad args: {exc.error_count()} issue(s)")
    try:
        return tool.fn(args, ctx, env)
    except Exception as exc:  # noqa: BLE001 — a broken tool shouldn't kill the turn
        return ToolResult(tool=call.tool, ok=False, error=f"{type(exc).__name__}: {exc}")
