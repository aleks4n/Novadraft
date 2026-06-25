"""LLM access point: role -> model registry, plus generic call helpers.

All agents call models exclusively through this module:
- `get_llm(role)` for a raw LangChain chat model
- `chat_json(...)` for a single-shot call that returns a validated JSON object
- `chat_json_with_tools(...)` for a LangGraph tool-calling loop that ends in
  a validated JSON object

When `response_schema` (a Pydantic model class) is passed:
- `chat_json` uses `llm.with_structured_output(schema)` — the model is forced
  via function calling to return exactly the schema. No text parsing needed.
- `chat_json_with_tools` runs the tool loop as normal (bind_tools conflicts with
  function-calling structured output), then does one final `with_structured_output`
  call over the full conversation. One extra LLM call; guarantees exact schema.

Without `response_schema` both helpers fall back to text parsing + no validation.

Reads provider API keys from the environment (`.env` at the novadraft root).
"""
from __future__ import annotations

import json
import operator
import os
import time
from pathlib import Path
from typing import Annotated, Callable, TypedDict

from pydantic import BaseModel, ValidationError

from dotenv import load_dotenv
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages

from ..common.utils.runlog import log_call

_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_ROOT / ".env")


def _unwrap_parameters(args: dict, schema: type[BaseModel]) -> dict:
    """DeepSeek occasionally nests forced tool-call args one level too deep,
    e.g. `{"parameters": {...actual fields...}}` instead of the flat fields.
    Detect that shape (a single "parameters" key holding a dict whose keys
    overlap the schema) and unwrap it before validation."""
    if set(args.keys()) == {"parameters"} and isinstance(args.get("parameters"), dict):
        return args["parameters"]
    return args


def _invoke_structured(llm, schema: type[BaseModel], messages) -> BaseModel:
    """`llm.with_structured_output(schema).invoke(messages)`, but recovers
    from DeepSeek's occasional extra `{"parameters": {...}}` nesting (see
    `_unwrap_parameters`) instead of raising a pydantic ValidationError."""
    try:
        return llm.with_structured_output(schema).invoke(messages)
    except ValidationError:
        bound = llm.bind_tools([schema], tool_choice=schema.__name__)
        resp = bound.invoke(messages)
        if not resp.tool_calls:
            raise
        args = _unwrap_parameters(resp.tool_calls[0]["args"], schema)
        return schema.model_validate(args)


# role -> "provider:model" string understood by init_chat_model
MODELS: dict[str, str] = {
    "reasoner": "deepseek:deepseek-reasoner",
    "cheap": "deepseek:deepseek-chat",
}


def _has_real_key() -> bool:
    return bool(os.getenv("DEEPSEEK_API_KEY") or os.getenv("OPENAI_API_KEY"))


def get_llm(role: str, temperature: float = 0.2):
    """Return a chat model for the given role.

    Falls back to a deterministic mock when no API key is configured so the
    demo is runnable out of the box.
    """
    if not _has_real_key():
        return _MockChat(role)

    from langchain.chat_models import init_chat_model

    llm = init_chat_model(MODELS[role], temperature=temperature)
    # Automatic failover: reasoner down -> retry on the cheap model.
    if role == "reasoner":
        llm = llm.with_fallbacks([init_chat_model(MODELS["cheap"], temperature=temperature)])
    return llm


# --- single-shot structured JSON --------------------------------------------

def chat_json(system: str, user: str, role: str = "reasoner",
               temperature: float = 0.2,
               response_schema: type[BaseModel] | None = None) -> dict:
    """Call the LLM once and return a parsed JSON object.

    When `response_schema` is given the model is bound via
    `with_structured_output` — it is forced through function calling to emit
    exactly the schema. No text parsing or regex heuristics needed.

    Without a schema the raw text is parsed loosely (handles markdown fences).
    """
    llm = get_llm(role, temperature=temperature)
    messages = [SystemMessage(content=system), HumanMessage(content=user)]

    t0 = time.time()
    if response_schema is not None and not isinstance(llm, _MockChat):
        try:
            result = _invoke_structured(llm, response_schema, messages)
        except Exception:
            # DeepSeek's reasoner intermittently rejects forced tool_choice
            # ("thinking mode does not support this tool_choice") — get_llm's
            # with_fallbacks doesn't reliably catch this once chained through
            # .with_structured_output(), so retry explicitly on the cheap
            # model (same workaround as chat_json_with_tools below).
            if role != "reasoner":
                raise
            cheap = get_llm("cheap", temperature=temperature)
            result = _invoke_structured(cheap, response_schema, messages)
        parsed = result.model_dump() if isinstance(result, BaseModel) else result
    else:
        resp = llm.invoke(messages)
        parsed = _parse_json_loose(resp.content)

    log_call(
        "llm", "chat_json", round(time.time() - t0, 3),
        role=role, temperature=temperature,
        schema=response_schema.__name__ if response_schema else None,
        prompt={"system": system, "user": user},
        output=parsed,
    )
    return parsed


# --- tool-calling loop --------------------------------------------------------

class _ToolLoopState(TypedDict):
    messages: Annotated[list, add_messages]
    trace: Annotated[list[dict], operator.add]
    steps: int


def chat_json_with_tools(system: str, user: str,
                          tools: list[dict],
                          tool_handlers: dict[str, Callable[[dict], str]],
                          role: str = "reasoner",
                          temperature: float = 0.2,
                          max_iters: int = 10,
                          response_schema: type[BaseModel] | None = None,
                          ) -> tuple[dict, list[dict]]:
    """Run a tool-use loop (LangGraph) then return a validated JSON object.

    `tools` is the OpenAI-style tools spec bound via `bind_tools`.
    `tool_handlers` maps function name to a sync callable returning a string.
    Returns `(result_dict, trace)` with one trace entry per tool call.

    When `response_schema` is given: after the tool loop finishes, one extra
    `with_structured_output` call is made over the full conversation history.
    This guarantees exact schema compliance without conflicting with bind_tools
    (function-calling structured output can't be stacked on top of bind_tools).
    """
    base_llm = get_llm(role, temperature=temperature)
    llm_with_tools = base_llm.bind_tools(tools)
    llm_plain = base_llm

    def call_model(state: _ToolLoopState) -> dict:
        if state["steps"] < max_iters:
            resp = llm_with_tools.invoke(state["messages"])
        else:
            messages = state["messages"] + [HumanMessage(content=(
                "You have reached the tool-call limit. Do NOT call any more "
                "tools. Respond now with the final JSON object as specified."
            ))]
            resp = llm_plain.invoke(messages)
        return {"messages": [resp], "steps": state["steps"] + 1}

    def call_tools(state: _ToolLoopState) -> dict:
        last = state["messages"][-1]
        tool_messages: list[ToolMessage] = []
        trace: list[dict] = []
        for call in last.tool_calls:
            handler = tool_handlers.get(call["name"])
            t0 = time.time()
            if handler is None:
                result = f"ERROR: unknown tool '{call['name']}'"
            else:
                try:
                    result = handler(call["args"])
                except Exception as exc:  # noqa: BLE001
                    result = f"ERROR: {exc}"
            duration_s = round(time.time() - t0, 3)
            tool_messages.append(ToolMessage(
                content=result, tool_call_id=call["id"], name=call["name"],
            ))
            trace.append({
                "name": call["name"],
                "args": call["args"],
                "result_chars": len(result),
            })
            log_call(
                "tool", call["name"], duration_s,
                args=call["args"], result_chars=len(result),
            )
        return {"messages": tool_messages, "trace": trace}

    def route(state: _ToolLoopState) -> str:
        last = state["messages"][-1]
        if isinstance(last, AIMessage) and last.tool_calls and state["steps"] <= max_iters:
            return "tools"
        return END

    graph = StateGraph(_ToolLoopState)
    graph.add_node("agent", call_model)
    graph.add_node("tools", call_tools)
    graph.set_entry_point("agent")
    graph.add_conditional_edges("agent", route, {"tools": "tools", END: END})
    graph.add_edge("tools", "agent")
    app = graph.compile()

    t0 = time.time()
    loop_result = app.invoke(
        {
            "messages": [SystemMessage(content=system), HumanMessage(content=user)],
            "trace": [],
            "steps": 0,
        },
        config={"recursion_limit": 2 * max_iters + 4},
    )
    trace = loop_result["trace"]

    if response_schema is not None and not isinstance(base_llm, _MockChat):
        # Final structured call over the full conversation — forces exact schema.
        # Can't do this inside the loop because bind_tools conflicts with
        # function-calling structured output.
        #
        # DeepSeek's reasoner intermittently rejects forced tool_choice once the
        # conversation already contains tool-call messages ("thinking mode does
        # not support this tool_choice"). get_llm's with_fallbacks does not
        # reliably catch this once chained through .bind_tools()/
        # .with_structured_output(), so retry explicitly on the cheap model.
        try:
            result = _invoke_structured(base_llm, response_schema, loop_result["messages"])
        except Exception:
            # The cheap model's structured-output binding only knows the schema
            # function — feeding it the raw tool_calls/ToolMessage objects from
            # the reasoner's loop confuses its output parser (it tries to match
            # an old tool name like "get_section" against the schema-only
            # binding). Flatten the transcript to plain text instead.
            flat = [SystemMessage(content=system),
                    HumanMessage(content=user + "\n\n" + _flatten_tool_transcript(loop_result["messages"]))]
            cheap = get_llm("cheap", temperature=temperature)
            result = _invoke_structured(cheap, response_schema, flat)
        parsed = result.model_dump() if isinstance(result, BaseModel) else result
    else:
        final = loop_result["messages"][-1]
        parsed = _parse_json_loose(final.content)

    log_call(
        "llm", "chat_json_with_tools", round(time.time() - t0, 3),
        role=role, temperature=temperature,
        schema=response_schema.__name__ if response_schema else None,
        prompt={"system": system, "user": user},
        output=parsed, n_tool_calls=len(trace),
    )
    return parsed, trace


def _flatten_tool_transcript(messages: list) -> str:
    """Render a tool-loop's AIMessage/ToolMessage history as plain text.

    Used only for the cheap-model fallback in chat_json_with_tools, so its
    structured-output binding (which only knows the response schema) never
    sees tool_call objects naming tools it doesn't recognize.
    """
    lines = ["# Tool calls made while researching this paper"]
    for m in messages[2:]:  # skip the original system/human prompt
        if isinstance(m, AIMessage) and m.tool_calls:
            for call in m.tool_calls:
                lines.append(f"\n## Called {call['name']}({call['args']})")
        elif isinstance(m, ToolMessage):
            lines.append(f"Result: {m.content}")
        elif isinstance(m, AIMessage) and m.content:
            lines.append(f"\n## Assistant note\n{m.content}")
    lines.append("\nBased on the above, respond now with the final JSON object.")
    return "\n".join(lines)


def _parse_json_loose(text: str) -> dict:
    """Parse a JSON object that may be wrapped in markdown fences or prose."""
    text = (text or "").strip()
    if not text:
        return {}
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        text = text[start:end + 1]
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise json.JSONDecodeError(
            f"{exc.msg} | raw response prefix: {text[:500]!r}",
            exc.doc, exc.pos,
        ) from exc


class _MockChat:
    """Deterministic offline stand-in for a LangChain chat model.

    Used when no provider API key is configured so dev runs stay functional
    without network access. Always replies with an empty JSON object and
    never emits tool calls.
    """

    def __init__(self, role: str):
        self.role = role

    def bind(self, **_kwargs):
        return self

    def bind_tools(self, _tools):
        return self

    def with_fallbacks(self, _fallbacks):
        return self

    def with_structured_output(self, _schema):
        return self

    def invoke(self, _messages):
        return AIMessage(content="{}")
