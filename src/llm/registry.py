"""LLM access point: role -> model registry, plus generic call helpers.

All agents call models exclusively through this module:
- `get_llm(role)` for a raw LangChain chat model
- `chat_json(...)` for a single-shot call that returns a parsed JSON object
- `chat_json_with_tools(...)` for a LangGraph tool-calling loop that ends in
  a JSON object

Reads provider API keys from the environment (`.env` at the novadraft root).
"""
from __future__ import annotations

import json
import operator
import os
from pathlib import Path
from typing import Annotated, Callable, TypedDict

from dotenv import load_dotenv
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages

_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_ROOT / ".env")

# role -> "provider:model" string understood by init_chat_model
MODELS: dict[str, str] = {
    "reasoner": "deepseek:deepseek-reasoner",
    "cheap": "deepseek:deepseek-chat",
}


def _has_real_key() -> bool:
    return bool(os.getenv("DEEPSEEK_API_KEY") or os.getenv("OPENAI_API_KEY"))


def get_llm(role: str):
    """Return a chat model for the given role.

    Falls back to a deterministic mock when no API key is configured so the
    demo is runnable out of the box.
    """
    if not _has_real_key():
        return _MockChat(role)

    from langchain.chat_models import init_chat_model

    llm = init_chat_model(MODELS[role])
    # Automatic failover: reasoner down -> retry on the cheap model.
    if role == "reasoner":
        llm = llm.with_fallbacks([init_chat_model(MODELS["cheap"])])
    return llm


# --- single-shot structured JSON --------------------------------------------

def chat_json(system: str, user: str, role: str = "reasoner",
               temperature: float = 0.2) -> dict:
    """Call the LLM once and parse a JSON object from the reply."""
    llm = get_llm(role).bind(temperature=temperature)
    resp = llm.invoke([
        SystemMessage(content=system),
        HumanMessage(content=user),
    ])
    return _parse_json_loose(resp.content)


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
                          max_iters: int = 10) -> tuple[dict, list[dict]]:
    """Run a tool-use loop (as a small LangGraph) and parse the final reply.

    `tools` is the OpenAI-style tools spec, bound directly via `bind_tools`.
    `tool_handlers` maps function name to a sync callable taking parsed args
    and returning a string. Returns `(parsed_json, trace)`, with one trace
    entry per tool call made.
    """
    base_llm = get_llm(role)
    llm_with_tools = base_llm.bind_tools(tools).bind(temperature=temperature)
    llm_plain = base_llm.bind(temperature=temperature)

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
            if handler is None:
                result = f"ERROR: unknown tool '{call['name']}'"
            else:
                try:
                    result = handler(call["args"])
                except Exception as exc:  # noqa: BLE001
                    result = f"ERROR: {exc}"
            tool_messages.append(ToolMessage(
                content=result, tool_call_id=call["id"], name=call["name"],
            ))
            trace.append({
                "name": call["name"],
                "args": call["args"],
                "result_chars": len(result),
            })
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

    result = app.invoke(
        {
            "messages": [SystemMessage(content=system), HumanMessage(content=user)],
            "trace": [],
            "steps": 0,
        },
        config={"recursion_limit": 2 * max_iters + 4},
    )
    final = result["messages"][-1]
    return _parse_json_loose(final.content), result["trace"]


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

    def invoke(self, _messages):
        return AIMessage(content="{}")
