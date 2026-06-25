"""Structured run logging, shared by every agent, subagent, and tool function.

Three calls, usable identically anywhere in the codebase:

    with start_run(category_slug, agent="paper_agent", inputs={...}) as run:
        ...
        run.output = {...}

    with step("fetch"):
        ...

    log_event("found arxiv id, fetching pdf", url=url)

``start_run`` is called once, by a *main* agent's entrypoint (e.g.
``paper_agent.run``). It opens a JSON log file under
``workspace/<category_slug>/logs/<agent>/<run_id>.json`` and writes it to disk
after every step/event, so it is always tailable while the run is in
progress.

``step`` and ``log_event`` are called the same way from main agents,
subagents, and plain tool functions alike — they don't know or need to know
whether a run is active:

- If a run is active (we're nested inside a ``start_run`` block, however
  deep), the call nests under whatever step/call is currently open, gets
  appended to the on-disk JSON, and is also printed to the terminal.
- If no run is active (e.g. a subagent is being exercised standalone, with
  no enclosing ``start_run``), the call is printed to the terminal only —
  no file is written. This is intentional: standalone subagent/tool runs
  don't get their own log file, they just get visible terminal output.

LLM calls are logged centrally from ``src/llm/registry.py`` (every agent
calls the LLM exclusively through that module), so prompts/responses show
up nested under whichever step triggered them without each agent needing to
log its own LLM calls.

See planning/technical-discussions/LOGGING_SYSTEM.md for the on-disk schema
and instructions for instrumenting a new agent.
"""
from __future__ import annotations

import contextvars
import json
import re
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import storage

_current_run: contextvars.ContextVar["_Run | None"] = contextvars.ContextVar(
    "runlog_current_run", default=None
)
_current_node: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "runlog_current_node", default=None
)
_current_depth: contextvars.ContextVar[int] = contextvars.ContextVar(
    "runlog_current_depth", default=0
)

_STATUS_GLYPH = {"running": "▶", "done": "✓", "error": "✗", "skipped": "—"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _slug(text: str, max_len: int = 40) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()
    return s[:max_len] or "run"


class _Run:
    def __init__(self, workspace: str, agent: str, inputs: dict, run_hint: str = ""):
        self.workspace = workspace
        self.agent = agent
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.run_id = f"{ts}_{_slug(run_hint)}" if run_hint else ts
        self.path = (
            storage.category_dir(workspace) / "logs" / agent / f"{self.run_id}.json"
        )
        self.lock = threading.Lock()
        self.output: Any = None
        self.data: dict = {
            "run_id": self.run_id,
            "agent": agent,
            "workspace": workspace,
            "started_at": _now_iso(),
            "finished_at": None,
            "duration_s": None,
            "inputs": inputs,
            "status": "running",
            "error": None,
            "output": None,
            "children": [],
        }
        self._t0 = time.time()
        self.flush()

    def flush(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        with open(tmp, "w") as f:
            json.dump(self.data, f, indent=2, default=str)
        tmp.replace(self.path)

    def finish(self, status: str, error: str | None = None) -> None:
        self.data["status"] = status
        self.data["error"] = error
        self.data["output"] = self.output
        self.data["finished_at"] = _now_iso()
        self.data["duration_s"] = round(time.time() - self._t0, 3)
        self.flush()


def _print(prefix: str, status: str, name: str, suffix: str = "") -> None:
    glyph = _STATUS_GLYPH.get(status, "•")
    print(f"{prefix}{glyph} {name} {status}{suffix}", flush=True)


def _indent(depth: int) -> str:
    return "  " * depth + ("└─ " if depth else "")


@contextmanager
def start_run(workspace: str, agent: str, inputs: dict, run_hint: str | None = None):
    """Open a new top-level run log for a main agent's entrypoint.

    ``run_hint`` (optional) is folded into the log filename, e.g. a query or
    paper id, so files are recognizable at a glance. Falls back to the
    first string-ish value in ``inputs``.
    """
    if run_hint is None:
        run_hint = next((v for v in inputs.values() if isinstance(v, str)), "")
    run = _Run(workspace, agent, inputs, run_hint=run_hint)
    run_token = _current_run.set(run)
    node_token = _current_node.set(None)  # root: children attach to run.data directly
    depth_token = _current_depth.set(0)
    print(f"\n=== {agent} [{workspace}] run {run.run_id} ===", flush=True)
    try:
        yield run
    except Exception as exc:  # noqa: BLE001
        run.finish("error", error=f"{type(exc).__name__}: {exc}")
        print(f"=== {agent} [{workspace}] FAILED ({run.data['duration_s']}s) ===\n", flush=True)
        raise
    else:
        run.finish("ok")
        print(f"=== {agent} [{workspace}] done ({run.data['duration_s']}s) ===\n", flush=True)
    finally:
        _current_run.reset(run_token)
        _current_node.reset(node_token)
        _current_depth.reset(depth_token)


def _attach(node: dict) -> None:
    """Append ``node`` under whatever's currently open (a step, or the run root)."""
    run = _current_run.get()
    if run is None:
        return
    parent = _current_node.get()
    with run.lock:
        if parent is None:
            run.data["children"].append(node)
        else:
            parent["children"].append(node)
        run.flush()


@contextmanager
def step(name: str, **data):
    """Wrap one pipeline stage. Nests under any enclosing ``step``/run.

    Prints ``name running`` on entry and ``name done <Xs>`` (or ``error``) on
    exit. If no run is active, this only prints — nothing is written to
    disk. Usable from main agents, subagents, and plain functions alike.
    """
    depth = _current_depth.get()
    _print(_indent(depth), "running", name)

    node = {
        "type": "step",
        "name": name,
        "status": "running",
        "started_at": _now_iso(),
        "finished_at": None,
        "duration_s": None,
        "data": data,
        "children": [],
    }
    _attach(node)

    node_token = _current_node.set(node)
    depth_token = _current_depth.set(depth + 1)
    t0 = time.time()
    try:
        yield node
    except Exception as exc:  # noqa: BLE001
        node["status"] = "error"
        node["data"]["error"] = f"{type(exc).__name__}: {exc}"
        node["duration_s"] = round(time.time() - t0, 3)
        node["finished_at"] = _now_iso()
        _print(_indent(depth), "error", name, f" ({node['duration_s']}s)")
        run = _current_run.get()
        if run is not None:
            with run.lock:
                run.flush()
        raise
    else:
        node["status"] = "done"
        node["duration_s"] = round(time.time() - t0, 3)
        node["finished_at"] = _now_iso()
        _print(_indent(depth), "done", name, f" ({node['duration_s']}s)")
        run = _current_run.get()
        if run is not None:
            with run.lock:
                run.flush()
    finally:
        _current_node.reset(node_token)
        _current_depth.reset(depth_token)


def skip_step(name: str, reason: str = "") -> None:
    """Record a step that was skipped outright (never entered)."""
    depth = _current_depth.get()
    _print(_indent(depth), "skipped", name, f" ({reason})" if reason else "")
    node = {
        "type": "step",
        "name": name,
        "status": "skipped",
        "started_at": _now_iso(),
        "finished_at": _now_iso(),
        "duration_s": 0.0,
        "data": {"reason": reason} if reason else {},
        "children": [],
    }
    _attach(node)


def log_event(message: str, **data) -> None:
    """Fine-grained note within the current step (or run root).

    Always printed to the terminal. Always written to the active run's JSON
    log, if one is active — per design, ``log_event`` calls are never
    dropped from the log the way a no-run ``step`` call is.
    """
    depth = _current_depth.get()
    _print(_indent(depth), "info", message)
    node = {
        "type": "event",
        "name": message,
        "status": "info",
        "started_at": _now_iso(),
        "finished_at": _now_iso(),
        "duration_s": None,
        "data": data,
        "children": [],
    }
    _attach(node)


def log_call(call_type: str, name: str, duration_s: float, status: str = "done", **data) -> None:
    """Record an already-finished call (e.g. one LLM round-trip) as a leaf
    node under the current step. Used by ``src/llm/registry.py`` so every
    prompt sent by any agent shows up nested under whichever step triggered
    it, with no changes needed in the agents themselves.
    """
    depth = _current_depth.get()
    _print(_indent(depth), status, f"{call_type}:{name}", f" ({duration_s}s)")
    node = {
        "type": call_type,
        "name": name,
        "status": status,
        "started_at": _now_iso(),
        "finished_at": _now_iso(),
        "duration_s": duration_s,
        "data": data,
        "children": [],
    }
    _attach(node)


def log_path_for(workspace: str, agent: str) -> Path:
    """Directory a given agent's run logs are written to, for a workspace."""
    return storage.category_dir(workspace) / "logs" / agent
