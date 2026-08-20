"""What the orchestrator can do, and how the planner learns about it.

The orchestrator is deliberately *not* constrained to a fixed set of operations: it is
given a goal and a catalogue of tools and composes them itself. That makes this catalogue
the real specification — `catalog()` renders it straight into the planner's prompt, so
what a tool declares is what the orchestrator knows how to do.

This module is only the machinery. Each tool lives in its own module and exports a
`TOOL`, so adding one is a new file plus a line below, and integrating a real subagent is
a change confined to that tool's file.
"""

from __future__ import annotations

from typing import Literal, get_args, get_origin

from pydantic import ValidationError

from src.common.schemas.context import Context
from src.agents.orchestrator_agent.schemas import ToolCall, ToolResult
from src.agents.orchestrator_agent.tools import creative_thinker, investigator
from src.agents.orchestrator_agent.tools.base import Tool, ToolEnv

__all__ = ["Tool", "ToolEnv", "TOOLS", "catalog", "dispatch"]

TOOLS: dict[str, Tool] = {
    tool.name: tool for tool in (creative_thinker.TOOL, investigator.TOOL)
}


def _type_name(annotation) -> str:
    """A readable type for the planner: Literals become their allowed values."""
    if get_origin(annotation) is Literal:
        return " | ".join(repr(v) for v in get_args(annotation))
    if get_origin(annotation) is list:
        inner = get_args(annotation)
        return f"list[{_type_name(inner[0])}]" if inner else "list"
    return getattr(annotation, "__name__", str(annotation))


def catalog() -> str:
    """The tool list as the planner sees it — rendered into the plan prompt.

    Generated from each tool's own args model, so allowed values, defaults and field
    descriptions reach the planner instead of being paraphrased in the prose (where the
    two drift). Adding an argument is then a one-place change.
    """
    lines = []
    for tool in TOOLS.values():
        lines.append(f"- {tool.name}")
        lines.append(f"    {tool.description}")
        lines.append("    arguments:")
        for name, field in tool.args_schema.model_fields.items():
            # get_default resolves default_factory fields, which otherwise render as
            # PydanticUndefined.
            default = ("" if field.is_required()
                       else f" (default {field.get_default(call_default_factory=True)!r})")
            desc = f"\n          {field.description}" if field.description else ""
            lines.append(f"      {name}: {_type_name(field.annotation)}{default}{desc}")
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
        return ToolResult(tool=call.tool, ok=False,
                          error=f"bad args: {exc.error_count()} issue(s)")
    try:
        return tool.fn(args, ctx, env)
    except Exception as exc:  # noqa: BLE001 — a broken tool shouldn't kill the turn
        return ToolResult(tool=call.tool, ok=False, error=f"{type(exc).__name__}: {exc}")
