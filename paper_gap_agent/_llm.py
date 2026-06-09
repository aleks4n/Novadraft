"""Shared litellm wrapper.

Reads model + API keys from environment (.env at project root). Returns the
parsed JSON content for structured-output calls.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Callable

from dotenv import load_dotenv
from litellm import completion

_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(_ROOT / ".env")

DEFAULT_MODEL = os.environ.get("NOVADRAFT_MODEL", "deepseek/deepseek-chat")


def chat_json(system: str, user: str, model: str | None = None,
              temperature: float = 0.2) -> dict:
    """Call the LLM and parse a JSON object from the reply."""
    resp = completion(
        model=model or DEFAULT_MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=temperature,
        response_format={"type": "json_object"},
    )
    text = resp["choices"][0]["message"]["content"]
    return json.loads(text)


def chat_json_with_tools(system: str, user: str,
                         tools: list[dict],
                         tool_handlers: dict[str, Callable[[dict], str]],
                         model: str | None = None,
                         temperature: float = 0.2,
                         max_iters: int = 10) -> tuple[dict, list[dict]]:
    """Run a tool-use loop and parse the final assistant message as JSON.

    `tools` is the OpenAI-style tools spec. `tool_handlers` maps function name
    to a sync callable taking parsed args, returning a string. Returns
    (parsed_json, trace) where trace is one entry per tool call made.
    """
    messages: list[dict] = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    trace: list[dict] = []

    for _ in range(max_iters):
        resp = completion(
            model=model or DEFAULT_MODEL,
            messages=messages,
            temperature=temperature,
            tools=tools,
            tool_choice="auto",
        )
        msg = resp["choices"][0]["message"]
        tool_calls = msg.get("tool_calls") if isinstance(msg, dict) else getattr(msg, "tool_calls", None)

        if not tool_calls:
            content = msg["content"] if isinstance(msg, dict) else msg.content
            return _parse_json_loose(content), trace

        messages.append(msg if isinstance(msg, dict) else msg.model_dump())

        for call in tool_calls:
            fn = call["function"] if isinstance(call, dict) else call.function
            name = fn["name"] if isinstance(fn, dict) else fn.name
            raw_args = fn["arguments"] if isinstance(fn, dict) else fn.arguments
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
            except json.JSONDecodeError:
                args = {}

            handler = tool_handlers.get(name)
            if handler is None:
                result = f"ERROR: unknown tool '{name}'"
            else:
                try:
                    result = handler(args)
                except Exception as exc:  # noqa: BLE001
                    result = f"ERROR: {exc}"

            trace.append({
                "name": name,
                "args": args,
                "result_chars": len(result),
            })
            call_id = call["id"] if isinstance(call, dict) else call.id
            messages.append({
                "role": "tool",
                "tool_call_id": call_id,
                "name": name,
                "content": result,
            })

    # Out of tool-call rounds: force a final answer without tools.
    messages.append({
        "role": "user",
        "content": (
            "You have reached the tool-call limit. Do NOT call any more tools. "
            "Respond now with the final JSON object as specified."
        ),
    })
    resp = completion(
        model=model or DEFAULT_MODEL,
        messages=messages,
        temperature=temperature,
    )
    msg = resp["choices"][0]["message"]
    content = msg["content"] if isinstance(msg, dict) else msg.content
    return _parse_json_loose(content), trace


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
