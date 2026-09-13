"""LangGraph-native run tracing.

Inside a running LangGraph graph, :func:`emit` pushes a structured event into the
graph's custom stream (``stream_mode="custom"``). A consumer reads that stream
alongside ``"updates"`` and reassembles a nested step tree: graph nodes are the
steps, and the LLM/tool/event leaves emitted here nest underneath whichever node
emitted them.

Each event is auto-tagged with the executing node name (``_node``), read from
LangGraph's run config, so a leaf attaches to the right step without any
contextvar bookkeeping.

Outside a graph run — a leaf subagent called directly, an ``.invoke()`` path,
tests — there is no active stream, so :func:`emit` is a silent no-op. An agent's
``run()`` instead streams the graph through
:class:`src.common.utils.logging.runlog.GraphRunLog`, which makes the custom stream
live: the same ``emit`` calls then land in the run's ``.jsonl``, on the terminal,
and over SSE in the app.

LLM and tool calls are emitted centrally from ``src/llm/registry.py``, so
prompts/responses show up nested under whichever node triggered them with no
tracing code in the agents themselves.
"""
from __future__ import annotations

from typing import Any

from langgraph.config import get_config, get_stream_writer


def emit(event: dict[str, Any]) -> None:
    """Emit one trace event into the active graph's custom stream.

    ``event`` mirrors a ``runlog`` leaf: at least ``type`` (``"llm"`` | ``"tool"``
    | ``"event"``) and ``name``, optionally ``duration_s`` and ``data``. The
    current LangGraph node name is attached as ``_node`` so the UI can nest the
    leaf under the right step.

    A no-op when called outside a running graph (``get_stream_writer`` returns a
    no-op writer, or raises on older LangGraph — both are tolerated), so the same
    code path is safe in standalone/CLI/eval/test contexts.
    """
    try:
        writer = get_stream_writer()
    except Exception:
        return
    if writer is None:
        return
    try:
        node = (get_config().get("metadata") or {}).get("langgraph_node")
    except Exception:
        node = None
    writer({**event, "_node": node})


def log_event(message: str, **data) -> None:
    """Emit a fine-grained informational note as an ``event`` leaf.

    A convenience wrapper over :func:`emit` for deterministic tool functions
    (e.g. the fetch/extract common tools) that want to narrate progress.
    Renders as a plain terminal-style line nested under the current node's
    step. No-op outside a running graph.
    """
    emit({"type": "event", "name": message, "data": data})
