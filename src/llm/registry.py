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
import json
import random
import sys
import time
import uuid
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
    "reasoner": "deepseek:deepseek-v4-pro",
    "cheap": "deepseek:deepseek-v4-flash"
}

# --- failure retry ---------------------------------------------------
# Every chat* call retries provider hiccups (rate limits, "service is too busy"
# 5xxs, timeouts, dropped connections) with exponential backoff before giving up.

_RETRIES = 4
_BACKOFF_BASE = 2.0  # 2s, 4s, 8s, 16s (+ jitter)
_STRUCTURED_ATTEMPTS = 3  # re-asks when a thinking model answers in prose instead of calling the tool
_TRANSIENT_STATUS = {429, 500, 502, 503, 504}
_TRANSIENT_NAMES = {"APITimeoutError", "APIConnectionError"}  # carry no status_code


def _is_transient(exc: Exception) -> bool:
    if getattr(exc, "status_code", None) in _TRANSIENT_STATUS:
        return True
    return type(exc).__name__ in _TRANSIENT_NAMES


def _telemetry(name: str, **data) -> None:
    emit_trace({"type": "event", "name": name, "data": data})


def _invoke_with_retry(invoke, *, call_id: str = "", operation: str = "llm",
                       structured_attempt: int | None = None):
    """Run `invoke`, retrying transient failures with exponential backoff. 
    The random jitter added to each delay desynchronizes parallel callers, 
    so calls that failed together don't all retry in one wave.
    """
    for attempt in range(_RETRIES + 1):
        attempt_started = time.time()
        _telemetry(
            "llm provider attempt started", call_id=call_id, operation=operation,
            attempt=attempt + 1, max_attempts=_RETRIES + 1,
            structured_attempt=structured_attempt,
        )
        try:
            result = invoke()
            _telemetry(
                "llm provider attempt finished", call_id=call_id, operation=operation,
                attempt=attempt + 1, max_attempts=_RETRIES + 1,
                structured_attempt=structured_attempt,
                duration_s=round(time.time() - attempt_started, 3),
            )
            return result
        except Exception as exc:
            transient = _is_transient(exc)
            _telemetry(
                "llm provider attempt failed", call_id=call_id, operation=operation,
                attempt=attempt + 1, max_attempts=_RETRIES + 1,
                structured_attempt=structured_attempt,
                duration_s=round(time.time() - attempt_started, 3),
                error_type=type(exc).__name__, status_code=getattr(exc, "status_code", None),
                transient=transient,
            )
            if attempt >= _RETRIES or not transient:
                raise
            delay = _BACKOFF_BASE * (2 ** attempt) + random.random()
            _telemetry(
                "llm retry scheduled", call_id=call_id, operation=operation,
                retry=attempt + 1, delay_s=round(delay, 3),
                structured_attempt=structured_attempt,
            )
            print(f"[llm] transient {type(exc).__name__} — retrying in {delay:.1f}s "
                  f"({attempt + 1}/{_RETRIES})", file=sys.stderr)
            time.sleep(delay)


def get_llm(role: str, temperature: float = 0.2,
            reasoning_effort: str | None = None,
            max_tokens: int | None = None):
    """Return a chat model for `role`.

    The `reasoner` model is wrapped with a fallback to the `cheap` model, so a
    transient reasoner failure (including ones surfaced by structured output)
    retries on the cheap model automatically.

    Falls back to a deterministic offline mock when no API key is configured.
    """
    if not (os.getenv("DEEPSEEK_API_KEY") or os.getenv("OPENAI_API_KEY")):
        return _MockChat()

    from langchain.chat_models import init_chat_model

    model_options = {"temperature": temperature}
    if reasoning_effort is not None:
        model_options["reasoning_effort"] = reasoning_effort
    if max_tokens is not None:
        model_options["max_tokens"] = max_tokens
    llm = init_chat_model(MODELS[role], **model_options)
    if role == "reasoner":
        cheap = init_chat_model(MODELS["cheap"], **model_options)
        llm = llm.with_fallbacks([cheap])
    return llm


def chat(llm, system: str, user: str) -> str:
    """One call on `llm` — the replacement for `llm.invoke(...)`.

    Returns the raw response text.
    """
    t0 = time.time()
    call_id = f"llm_{uuid.uuid4().hex[:10]}"
    _telemetry("llm call started", call_id=call_id, operation="chat",
               schema=None, structured_attempts=1)
    messages = [SystemMessage(content=system), HumanMessage(content=user)]
    text = _invoke_with_retry(lambda: llm.invoke(messages), call_id=call_id,
                              operation="chat").content
    _trace("chat", system, user, schema=None, output=text, t0=t0)
    _telemetry("llm call finished", call_id=call_id, operation="chat",
               duration_s=round(time.time() - t0, 3))
    return text


def chat_with_structured_output(llm, system: str, user: str, schema: type[T]) -> T:
    """One call on `llm` — the replacement for
    `llm.with_structured_output(schema).invoke(...)`.

    The model is steered via function calling to return exactly `schema`, and
    the validated instance is returned. Provider-specific envelopes around the
    tool-call arguments are unwrapped by `_normalize_structured_args` rather than
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
    call_id = f"llm_{uuid.uuid4().hex[:10]}"
    _telemetry("llm call started", call_id=call_id,
               operation="chat_with_structured_output", schema=schema.__name__,
               structured_attempts=_STRUCTURED_ATTEMPTS)
    if isinstance(llm, _MockChat):
        # Offline dev run: skip tool binding and return an unvalidated instance.
        result = schema.model_construct()
        _trace("chat_with_structured_output", system, user, schema=schema,
               output=result.model_dump(), t0=t0)
        _telemetry("llm call finished", call_id=call_id,
                   operation="chat_with_structured_output", schema=schema.__name__,
                   duration_s=round(time.time() - t0, 3), offline=True)
        return result

    bound = llm.bind_tools([schema], tool_choice="auto")
    args = None
    for attempt in range(_STRUCTURED_ATTEMPTS):
        _telemetry("llm structured attempt started", call_id=call_id,
                   schema=schema.__name__, attempt=attempt + 1,
                   max_attempts=_STRUCTURED_ATTEMPTS)
        resp = _invoke_with_retry(
            lambda: bound.invoke(messages), call_id=call_id,
            operation="chat_with_structured_output", structured_attempt=attempt + 1,
        )
        if resp.tool_calls:
            args = _normalize_structured_args(resp.tool_calls[0]["args"], schema)
            break
        # Thinking-mode models sometimes answer with the JSON inline (as prose)
        # instead of emitting a tool call — recover by parsing it from the text.
        args = _json_from_text(resp.content)
        if args is not None:
            break
        # Neither: the model just answered in prose. Since `tool_choice` can't be
        # forced on these models, the only lever is asking again — measured at
        # roughly 1 call in 3 on deepseek-reasoner, so one attempt isn't enough.
        if attempt < _STRUCTURED_ATTEMPTS - 1:
            _telemetry("llm structured reask", call_id=call_id,
                       schema=schema.__name__, completed_attempt=attempt + 1,
                       next_attempt=attempt + 2, reason="no structured tool call or JSON")
            print(f"[llm] {schema.__name__}: no tool call in response — retrying "
                  f"({attempt + 1}/{_STRUCTURED_ATTEMPTS - 1})", file=sys.stderr)
            # This is a corrective re-ask, not an identical replay. Keeping the
            # assistant response preserves DeepSeek's reasoning_content while the new
            # user turn states exactly what was invalid and what must come next.
            messages = messages + [
                resp,
                HumanMessage(content=(
                    f"Your previous response could not be parsed as {schema.__name__}: "
                    "it contained neither a tool call nor a valid JSON object. Return "
                    f"the {schema.__name__} result now by calling the provided tool. "
                    "Do not answer with prose."
                )),
            ]
    if args is None:
        raise ValueError(
            f"{schema.__name__}: model returned no parseable structured output "
            f"in {_STRUCTURED_ATTEMPTS} attempts"
        )
    result = schema.model_validate(_normalize_structured_args(args, schema))

    _trace("chat_with_structured_output", system, user, schema=schema,
           output=result.model_dump(), t0=t0)
    _telemetry("llm call finished", call_id=call_id,
               operation="chat_with_structured_output", schema=schema.__name__,
               duration_s=round(time.time() - t0, 3))
    return result


def _normalize_structured_args(args, schema: type[BaseModel]):
    """Unwrap provider-specific envelopes around structured tool arguments."""
    if not isinstance(args, dict):
        return args

    fields = set(schema.model_fields)
    for wrapper in ("parameters", "critique", "info", "data", "result", "output"):
        value = args.get(wrapper)
        if wrapper not in fields and len(args) == 1 and value is not None:
            if isinstance(value, dict):
                return value
            if isinstance(value, str):
                try:
                    parsed = json.loads(value)
                except json.JSONDecodeError:
                    continue
                if isinstance(parsed, dict):
                    return parsed
    return args


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
