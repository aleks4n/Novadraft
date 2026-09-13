"""What the orchestrator can do, and how the planner learns about it.

The orchestrator is not constrained to a fixed set of operations: it is given a
goal and a catalogue of tools and composes them itself. ``catalog()`` renders this
registry straight into the planner's prompt, so what a tool declares is what the
orchestrator knows how to do.

Each tool lives in its own module and exports a ``TOOL``; adding one is a new file
plus a line below.
"""

from __future__ import annotations

from typing import Literal, get_args, get_origin

from pydantic import ValidationError

from src.common.schemas.context import Context
from src.agents.orchestrator_agent.schemas import ToolCall, ToolResult
from src.agents.orchestrator_agent.tools import creative_thinker, investigator
from src.agents.orchestrator_agent.tools.base import Tool, ToolEnv

__all__ = ["Tool", "ToolEnv", "TOOLS", "catalog", "dispatch", "tool_specs"]

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
            # Write numeric bounds into the field's own description ("between 1 and 8"),
            # not as notation here. Rendering them as `width: int [>= 1, <= 8]` makes the
            # planner answer in prose instead of emitting structured output at all.
            lines.append(f"      {name}: {_type_name(field.annotation)}{default}{desc}")
    return "\n".join(lines)


def _bounds(field) -> dict:
    """The numeric bounds pydantic put on a field, as plain numbers.

    Read off the field's own metadata rather than restated here, for the same reason
    `catalog()` renders the descriptions: an editor whose slider stops at a different
    number than the validator does is a form that can only be wrong.
    """
    out: dict = {}
    for entry in getattr(field, "metadata", []) or []:
        for attr, key in (("ge", "min"), ("le", "max"), ("gt", "min"), ("lt", "max")):
            value = getattr(entry, attr, None)
            if value is not None:
                out[key] = value + (1 if attr == "gt" else -1 if attr == "lt" else 0)
    return out


def _arg_spec(name: str, field) -> dict:
    """One argument, described well enough for a form to be generated from it.

    The same source as `catalog()`, which is the point: the planner is told what a tool
    takes by its args model, and so is the user editing what the planner chose. There is
    no second list of knobs to keep in step.
    """
    annotation = field.annotation
    origin, args = get_origin(annotation), get_args(annotation)
    kind, options = "text", []
    if origin is Literal:
        kind, options = "enum", [str(v) for v in args]
    elif origin is list:
        inner = args[0] if args else str
        if get_origin(inner) is Literal:
            kind, options = "enum_list", [str(v) for v in get_args(inner)]
        else:
            kind = "text_list"
    elif annotation is bool:
        kind = "bool"
    elif annotation is int:
        kind = "int"
    return {
        "name": name,
        "kind": kind,
        "options": options,
        "required": field.is_required(),
        "default": None if field.is_required() else field.get_default(
            call_default_factory=True),
        "description": field.description or "",
        **_bounds(field),
    }


def tool_specs() -> list[dict]:
    """Every tool and its arguments as JSON — what the UI renders the plan editor from.

    Deliberately the same projection the planner gets, in a different format: a client
    showing which agent runs with which parameters, and letting the user change them
    before the plan runs, must not be working from its own idea of what the arguments
    are. A tool gains an argument in one place and both surfaces grow it.
    """
    return [
        {
            "name": tool.name,
            "description": tool.description,
            "args": [_arg_spec(name, field)
                     for name, field in tool.args_schema.model_fields.items()],
        }
        for tool in TOOLS.values()
    ]


def validate_args(tool_name: str, args: dict) -> dict:
    """Coerce one step's arguments through the tool's own schema, or raise.

    The user may edit what the planner wrote, so the edited values need the same check
    the planner's do — and they get it from the same model, before the plan is stored,
    rather than failing halfway through a run the user has already started.
    """
    tool = TOOLS.get(tool_name)
    if tool is None:
        raise ValueError(f"unknown tool '{tool_name}'")
    return tool.args_schema.model_validate(args or {}).model_dump(mode="json")


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
