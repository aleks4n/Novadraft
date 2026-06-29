"""LLM access point: a role -> model registry plus a single `chat` helper.

`get_llm(role)` returns a LangChain chat model for a role; the two `chat*`
helpers make one call on it:

- `get_llm(role)` — the `reasoner` role gets an automatic fallback to the
  `cheap` model, composed so it also covers structured-output calls.
- `chat(llm, system, user)` — the replacement for `llm.invoke(...)`; returns
  the raw response text.
- `chat_with_structured_output(llm, system, user, schema)` — the replacement
  for `llm.with_structured_output(schema).invoke(...)`; the model is forced via
  function calling to return exactly `schema` (a pydantic model class) and the
  validated instance is returned — no text parsing needed.

Provider API keys are read from the environment (`.env` at the novadraft root).
When no key is set, `get_llm` returns a deterministic offline mock so dev runs
work without network access.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import TypeVar

from dotenv import load_dotenv
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import BaseModel

from ..common.utils.logging.trace import emit as emit_trace

_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_ROOT / ".env")

T = TypeVar("T", bound=BaseModel)

# role -> "provider:model" string understood by init_chat_model
MODELS: dict[str, str] = {
    "reasoner": "deepseek:deepseek-reasoner",
    "cheap": "deepseek:deepseek-chat",
}


def get_llm(role: str, temperature: float = 0.2):
    """Return a chat model for `role`.

    The `reasoner` model is wrapped with a fallback to the `cheap` model, so a
    transient reasoner failure (including ones surfaced by structured output)
    retries on the cheap model automatically.

    Falls back to a deterministic offline mock when no API key is configured.
    """
    if not (os.getenv("DEEPSEEK_API_KEY") or os.getenv("OPENAI_API_KEY")):
        return _MockChat()

    from langchain.chat_models import init_chat_model

    llm = init_chat_model(MODELS[role], temperature=temperature)
    if role == "reasoner":
        cheap = init_chat_model(MODELS["cheap"], temperature=temperature)
        llm = llm.with_fallbacks([cheap])
    return llm


def chat(llm, system: str, user: str) -> str:
    """One call on `llm` — the replacement for `llm.invoke(...)`.

    Returns the raw response text.
    """
    t0 = time.time()
    text = llm.invoke([SystemMessage(content=system), HumanMessage(content=user)]).content
    _trace("chat", system, user, schema=None, output=text, t0=t0)
    return text


def chat_with_structured_output(llm, system: str, user: str, schema: type[T]) -> T:
    """One call on `llm` — the replacement for
    `llm.with_structured_output(schema).invoke(...)`.

    The model is steered via function calling to return exactly `schema`, and
    the validated instance is returned. Recovers from DeepSeek's occasional
    `{"parameters": {...}}` nesting of the tool-call arguments instead of
    raising a pydantic ValidationError.

    `tool_choice` is left as ``"auto"`` rather than forcing the function call:
    DeepSeek's thinking-capable models (``deepseek-reasoner`` and the hybrid
    ``deepseek-chat``) reject any forced/required `tool_choice` with
    "Thinking mode does not support this tool_choice", which `400`s — and since
    both the reasoner and its `cheap` fallback are such models, the forced call
    fails on both branches. With a single bound tool and a prompt that asks for
    the structured result, "auto" reliably yields the tool call while staying
    within what thinking mode allows.
    """
    messages = [SystemMessage(content=system), HumanMessage(content=user)]

    t0 = time.time()
    if isinstance(llm, _MockChat):
        # Offline dev run: skip tool binding and return an unvalidated instance.
        result = schema.model_construct()
        _trace("chat_with_structured_output", system, user, schema=schema,
               output=result.model_dump(), t0=t0)
        return result

    resp = llm.bind_tools([schema], tool_choice="auto").invoke(messages)
    if resp.tool_calls:
        args = resp.tool_calls[0]["args"]
        if set(args) == {"parameters"} and isinstance(args.get("parameters"), dict):
            args = args["parameters"]
    else:
        # Thinking-mode models sometimes answer with the JSON inline (as prose)
        # instead of emitting a tool call — recover by parsing it from the text
        # before giving up.
        args = _json_from_text(resp.content)
        if args is None:
            raise ValueError(
                f"{schema.__name__}: model returned no parseable structured output"
            )
    result = schema.model_validate(args)

    _trace("chat_with_structured_output", system, user, schema=schema,
           output=result.model_dump(), t0=t0)
    return result


def _json_from_text(content) -> dict | None:
    """Best-effort extraction of a single JSON object from a model's text reply.

    Handles plain JSON, ```json fenced blocks, and JSON embedded in surrounding
    prose by scanning for the outermost balanced ``{...}`` span. Returns the
    parsed dict, or ``None`` if nothing parseable is found.
    """
    if isinstance(content, list):
        # Some providers return content as a list of parts; concatenate text.
        content = "".join(
            part.get("text", "") if isinstance(part, dict) else str(part)
            for part in content
        )
    if not isinstance(content, str) or not content.strip():
        return None

    import json
    import re

    text = content.strip()

    # Strip a ```json ... ``` (or bare ``` ... ```) fence if present.
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()

    # Fast path: the whole thing is one JSON object.
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        pass

    # Fallback: find the first balanced {...} span and try to parse it.
    start = text.find("{")
    while start != -1:
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(text[start : i + 1])
                        if isinstance(obj, dict):
                            return obj
                    except json.JSONDecodeError:
                        break  # malformed; advance to next "{"
                    break
        start = text.find("{", start + 1)
    return None


def _trace(name: str, system: str, user: str, *, schema, output, t0: float) -> None:
    """Emit one `runlog` LLM event for a `chat*` call."""
    emit_trace({
        "type": "llm", "name": name, "duration_s": round(time.time() - t0, 3),
        "data": {
            "schema": schema.__name__ if schema else None,
            "prompt": {"system": system, "user": user},
            "output": output,
        },
    })


class _MockChat:
    """Deterministic offline stand-in for a LangChain chat model, used when no
    provider API key is configured so dev runs stay functional without network
    access. Plain calls return empty text; structured calls return an empty
    (unvalidated) instance of the requested schema.

    `chat_with_structured_output` special-cases this type so it never has to
    fabricate a tool call — see the `isinstance(llm, _MockChat)` branch there."""

    def invoke(self, _messages) -> AIMessage:
        return AIMessage(content="")

    def with_structured_output(self, schema, **_kwargs):
        return _MockStructured(schema)


class _MockStructured:
    """What `_MockChat.with_structured_output` returns: `.invoke` yields an
    empty instance of the schema (fields left unset via `model_construct`)."""

    def __init__(self, schema: type[BaseModel]):
        self._schema = schema

    def invoke(self, _messages) -> BaseModel:
        return self._schema.model_construct()
