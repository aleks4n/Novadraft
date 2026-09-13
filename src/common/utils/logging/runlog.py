"""Append-only run log — the live stream and the on-disk history, in one pass.

A graph agent is streamed with::

    graph.stream(inputs, stream_mode=["updates", "custom"], subgraphs=True)

Each ``(namespace, mode, data)`` chunk is normalized by :class:`RunRecorder` into
one flat event, which is (a) appended as a single JSON line to the run's ``.jsonl``
file and (b) handed back to the caller to forward (over SSE) or echo (to a
terminal). There is no nested tree on the write side: the log is a linear event
stream, and any hierarchy (subgraph events under their parent, the live diagram's
current-step highlight) is reconstructed at read time on the frontend from each
event's ``ns``/``gnode`` fields.

The bytes written to disk are identical to the bytes streamed, so a live run and a
reopened past run render from the same source: live = forward each event as it is
recorded; history = read the file back with :func:`read_run`. Writing incrementally
(one flushed line per event) also means a crash leaves a complete log up to the
point of failure.

:class:`GraphRunLog` wraps the whole loop (pick the log path, draw the diagram,
stream, record, finish) so there is one implementation shared by every caller: an
agent's standalone ``run()`` (echo to terminal) and the app backend (forward each
event over SSE). Both produce identical logs.

Event shape (one JSON object per line)::

    {"seq": 0, "t": "...Z", "kind": "run_start",
     "agent": "paper_agent", "workspace": "transformers",
     "inputs": {...}, "mermaid": "graph TD; ..."}
    {"seq": 1, "t": "...", "kind": "node",  "ns": "", "node": "fetch",
     "gnode": "fetch", "status": "done", "result": {...}}        # inter-node state delta
    {"seq": 2, "t": "...", "kind": "llm",   "ns": "summarize:<uuid>",
     "node": "extractor", "gnode": "summarize:extractor",
     "name": "chat_with_structured_output", "duration_s": 3.8, "data": {...}}  # prompt + output
    {"seq": 3, "t": "...", "kind": "event", "ns": "summarize:<uuid>",
     "node": "critic", "gnode": "summarize:critic",
     "name": "will regenerate", "data": {...}}                    # domain note
    {"seq": 9, "t": "...", "kind": "run_end",
     "status": "done", "output": {...}, "error": null, "duration_s": 17.4}

``gnode`` is the id the event maps to in ``get_graph(xray=True)``: a top-level node
keeps its name (``fetch``), a subgraph-internal node is namespaced
(``summarize:extractor``) so the frontend can light up the right box.
"""
from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import IO, Any, Callable, Iterator

from src.common.utils import storage


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _ns_str(ns: tuple) -> str:
    """A streamed namespace tuple -> a stable string. ``()`` (the top-level
    graph) becomes ``""``; a subgraph invocation ``("summarize:<uuid>",)``
    becomes ``"summarize:<uuid>"`` (nested subgraphs join with ``/``)."""
    return "/".join(ns) if ns else ""


def _gnode(ns_str: str, node: str) -> str:
    """The ``get_graph(xray=True)`` node id this event highlights. Top-level
    nodes keep their bare name; a subgraph-internal node is prefixed with the
    owning top-level node (the part before ``:`` in the namespace), e.g.
    ns ``summarize:<uuid>`` + node ``extractor`` -> ``summarize:extractor``."""
    if not ns_str:
        return node
    owner = ns_str.split("/")[0].split(":")[0]
    return f"{owner}:{node}"


def slugify(text: str) -> str:
    """Filename-safe hint from arbitrary text (a query / paper id), capped to 40
    chars. Empty input becomes ``"run"``."""
    s = re.sub(r"[^A-Za-z0-9]+", "-", text or "").strip("-").lower()
    return s[:40] or "run"


class RunRecorder:
    """Streams graph chunks to a ``.jsonl`` file and yields them back as events.

    Construct with an open text file handle (``fh``). :meth:`start` writes the
    ``run_start`` header, :meth:`record` turns one streamed chunk into zero or
    more events (each already written to disk), and :meth:`finish` writes the
    ``run_end`` footer. Every method returns the event dict(s) it wrote so the
    caller can forward them over SSE — the file and the stream stay in lockstep.
    """

    def __init__(self, fh: IO[str], agent: str, workspace: str | None, inputs: dict):
        self._fh = fh
        self.agent = agent
        self.workspace = workspace
        self.inputs = inputs
        self._seq = 0
        self._t0 = time.time()
        self.output: Any = None  # last top-level "result" delta seen

    def _write(self, event: dict) -> dict:
        event = {"seq": self._seq, "t": _now_iso(), **event}
        self._seq += 1
        self._fh.write(json.dumps(event, default=str) + "\n")
        self._fh.flush()
        return event

    def start(self, mermaid: str) -> dict:
        return self._write({
            "kind": "run_start", "agent": self.agent, "workspace": self.workspace,
            "inputs": self.inputs, "mermaid": mermaid,
        })

    def record(self, ns: tuple, mode: str, data: Any) -> list[dict]:
        """Normalize one streamed chunk into flat event(s), persist, return them."""
        ns_str = _ns_str(ns)
        events: list[dict] = []

        if mode == "updates" and isinstance(data, dict):
            # ``{node: state_delta}`` — the node finished; the delta is exactly
            # what flows on to the next node (the inter-node state).
            for node, delta in data.items():
                if not ns and isinstance(delta, dict) and "result" in delta:
                    self.output = delta["result"]
                events.append(self._write({
                    "kind": "node", "ns": ns_str, "node": node,
                    "gnode": _gnode(ns_str, node), "status": "done", "result": delta,
                }))

        elif mode == "custom" and isinstance(data, dict):
            # A leaf emitted from inside a node via ``trace.emit`` (LLM call,
            # tool call, or a domain note), auto-tagged with its node (``_node``).
            node = data.get("_node") or "?"
            events.append(self._write({
                "kind": data.get("type", "event"), "ns": ns_str, "node": node,
                "gnode": _gnode(ns_str, node), "name": data.get("name"),
                "duration_s": data.get("duration_s"), "data": data.get("data") or {},
            }))

        return events

    def finish(self, output: Any = None, error: str | None = None) -> dict:
        return self._write({
            "kind": "run_end", "status": "error" if error else "done",
            "output": output if output is not None else self.output,
            "error": error, "duration_s": round(time.time() - self._t0, 3),
        })


def _echo(event: dict) -> None:
    """Print one terminal-friendly line for a streamed event (to stderr).

    Enough to watch a pipeline progress live without the full JSON; indented
    leaves nest visually under their node."""
    kind = event.get("kind")
    if kind == "run_start":
        line = f"▶ {event.get('agent')}  workspace={event.get('workspace') or '-'}"
    elif kind == "run_end":
        line = f"■ {event.get('status')}  {event.get('duration_s')}s"
        if event.get("error"):
            line += f"  {event['error']}"
    elif kind == "node":
        line = f"● {event.get('gnode')}"
    else:  # llm / tool / event leaf
        dur = event.get("duration_s")
        suffix = f"  {dur}s" if dur is not None else ""
        line = f"    ↳ {kind}: {event.get('name')}{suffix}"
    print(line, file=sys.stderr, flush=True)


class GraphRunLog:
    """One streamed graph run, persisted to a flat ``.jsonl`` as it streams.

    The single implementation of the stream→record→file loop. Construct it with
    the agent name and either a ``workspace`` slug (logs land in
    ``workspace/<slug>/logs/<agent>/``) or an explicit ``log_dir``. Then drive
    it one of two ways:

    - :meth:`stream` — a generator yielding every event (``run_start``, the
      node/llm/tool/event leaves, then ``run_end``). Forward each over SSE, or
      pass ``echo=True`` to also print it. Used by the app backend.
    - :meth:`run` — consume :meth:`stream` to completion and return the output.
      Used by a main agent's standalone entrypoint.

    After iteration, :attr:`output`, :attr:`error`, :attr:`log_path` and
    :attr:`run_id` hold the run result.
    """

    def __init__(self, agent: str, *, workspace: str | None = None,
                 log_dir: str | Path | None = None, run_hint: str = "run",
                 recursion_limit: int = 80):
        self.agent = agent
        self.workspace = workspace
        self.recursion_limit = recursion_limit
        self.run_id = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{slugify(run_hint)}"
        base = Path(log_dir) if log_dir is not None else storage.agent_log_dir(workspace, agent)
        base.mkdir(parents=True, exist_ok=True)
        self.log_path = base / f"{self.run_id}.jsonl"
        self.output: Any = None
        self.error: str | None = None

    def stream(self, graph, inputs: dict, *, log_inputs: dict | None = None,
               mermaid: str | None = None, echo: bool = False,
               on_event: Callable[[dict], None] | None = None) -> Iterator[dict]:
        """Stream ``graph`` over ``inputs``, writing each event to the ``.jsonl``
        and yielding it. ``log_inputs`` overrides what's recorded in the header
        when the form inputs differ from the graph inputs (an input adapter).
        ``echo`` prints each event; ``on_event`` is called per event. A crash
        inside the graph is captured into :attr:`error` and still finalizes the
        log."""
        if mermaid is None:
            mermaid = graph.get_graph(xray=True).draw_mermaid()

        def _out(ev: dict) -> dict:
            if echo:
                _echo(ev)
            if on_event is not None:
                on_event(ev)
            return ev

        with open(self.log_path, "w") as fh:
            rec = RunRecorder(fh, self.agent, self.workspace,
                              log_inputs if log_inputs is not None else inputs)
            yield _out(rec.start(mermaid))
            try:
                for ns, mode, data in graph.stream(
                    inputs, stream_mode=["updates", "custom"], subgraphs=True,
                    config={"recursion_limit": self.recursion_limit},
                ):
                    for ev in rec.record(ns, mode, data):
                        yield _out(ev)
            except Exception as e:  # noqa: BLE001 — capture, finalize, surface in footer
                self.error = f"{type(e).__name__}: {e}"
            footer = rec.finish(error=self.error)
            self.output = footer.get("output")
            yield _out(footer)

    def run(self, graph, inputs: dict, *, mermaid: str | None = None,
            echo: bool = True) -> Any:
        """Consume :meth:`stream` to completion, returning the run output."""
        for _ in self.stream(graph, inputs, mermaid=mermaid, echo=echo):
            pass
        return self.output


def read_run(path) -> dict:
    """Read a ``.jsonl`` run log back into ``{header, events, footer}``.

    ``header`` is the ``run_start`` event (carries the agent, inputs, and the
    Mermaid diagram), ``events`` is every node/llm/tool/event leaf in order, and
    ``footer`` is the ``run_end`` (status/output/error) — ``None`` if the run is
    still in progress / was never finished. This is the read-time counterpart to
    streaming: the frontend renders a reopened run exactly as it rendered live.
    """
    header: dict | None = None
    footer: dict | None = None
    events: list[dict] = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            ev = json.loads(line)
            kind = ev.get("kind")
            if kind == "run_start":
                header = ev
            elif kind == "run_end":
                footer = ev
            else:
                events.append(ev)
    return {"header": header, "events": events, "footer": footer}
