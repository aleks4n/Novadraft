"""Server-side run manager — agent runs detached from HTTP connections.

Previously a run executed *inside* the SSE response generator, so its lifetime
was tied to one browser request: a reload (or anything that dropped the
connection) killed the run mid-flight, and nothing survived a page refresh.

The manager fixes that architecture:

- **Detached execution** — ``start()`` launches the graph in a daemon thread that
  streams through :class:`RunRecorder` into the standard per-run ``.jsonl``
  (byte-identical to the dev harness / standalone logs). The HTTP layer only
  *observes*: ``GET /api/runs/{id}/stream`` replays the log file from any ``seq``
  and follows it live, so any number of clients can attach, detach, and reload
  freely without touching the run.
- **Cancel** — ``cancel()`` sets a per-run event checked between streamed chunks;
  the run stops after the current step (a long LLM call finishes first), writes a
  ``run_end`` footer with ``error="cancelled"``, and can later be *restarted*
  (``restart()`` re-runs the same inputs — stage folders / paper.json are the
  checkpoints, so completed work is skipped).
- **Persistence** — every run's metadata (query, kind, status, target,
  candidates, log file) lives in ``workspace/<slug>/logs/app_runs.json``. On
  backend restart, entries still marked ``running`` become ``interrupted``
  (resumable); the frontend re-populates its queue from this index after a
  reload.
- **Duplicate guard** — starting a run whose (slug, agent, query) matches one
  already active returns the existing entry instead of racing a second copy.

Gap-level double-processing across parallel runs is prevented one layer down by
``src/common/utils/claims.py``; this module is about run lifecycle, not work
claiming.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from dev.registry import GRAPH_BUILDERS, INPUT_ADAPTERS
from dev.runlog import RunRecorder
from src.common.utils import storage
from src.common.utils.logging.runlog import slugify

# Mirrors the pipeline's outer super-step budget (see src/graph/pipeline.py).
GRAPH_RECURSION_LIMIT = 300

# Terminal entries kept in a workspace's index (oldest pruned first).
_MAX_FINISHED = 50

# Statuses that count as "active" (an equal query may not start a second copy,
# and the run cannot be deleted). "ambiguous"/"needs_source" are *paused* —
# terminal for the thread, but waiting on user input.
_ACTIVE = {"running"}
_PAUSED = {"ambiguous", "needs_source"}


class _Cancelled(Exception):
    """Raised inside the run loop when the user cancelled the run."""


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _index_path(slug: str) -> Path:
    return storage.category_dir(slug) / "logs" / "app_runs.json"


class RunManager:
    """Registry + executor for app-triggered agent runs. One per process."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._runs: dict[str, dict] = {}          # run_id -> meta (the index entry)
        self._cancels: dict[str, threading.Event] = {}
        self._loaded_slugs: set[str] = set()

    # --- index persistence ----------------------------------------------------

    def _load_slug(self, slug: str) -> None:
        """Pull a workspace's persisted runs into memory once. Entries that were
        ``running`` when the previous backend died become ``interrupted``."""
        if slug in self._loaded_slugs:
            return
        self._loaded_slugs.add(slug)
        path = _index_path(slug)
        if not path.exists():
            return
        try:
            entries = json.loads(path.read_text())
        except Exception:  # noqa: BLE001 — a corrupt index shouldn't brick the app
            return
        for meta in entries:
            rid = meta.get("run_id")
            if not rid or rid in self._runs:
                continue
            if meta.get("status") in _ACTIVE:
                meta["status"] = "interrupted"
                meta["error"] = "backend restarted mid-run"
            self._runs[rid] = meta

    def _persist_slug(self, slug: str) -> None:
        """Write a workspace's entries back to its index (newest last), pruning
        old terminal entries. Caller holds the lock."""
        entries = [m for m in self._runs.values() if m.get("slug") == slug]
        entries.sort(key=lambda m: m.get("created_at") or "")
        finished = [m for m in entries if m["status"] not in _ACTIVE | _PAUSED]
        for stale in finished[:-_MAX_FINISHED] if len(finished) > _MAX_FINISHED else []:
            entries.remove(stale)
            self._runs.pop(stale["run_id"], None)
        path = _index_path(slug)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(entries, indent=1, default=str))

    def _update(self, run_id: str, **patch: Any) -> dict:
        with self._lock:
            meta = self._runs[run_id]
            meta.update(patch, updated_at=_now_iso())
            self._persist_slug(meta["slug"])
            return dict(meta)

    # --- queries ----------------------------------------------------------------

    def list(self, slug: str) -> list[dict]:
        with self._lock:
            self._load_slug(slug)
            entries = [dict(m) for m in self._runs.values() if m.get("slug") == slug]
        entries.sort(key=lambda m: m.get("created_at") or "")
        return entries

    def get(self, run_id: str) -> dict | None:
        with self._lock:
            meta = self._runs.get(run_id)
            return dict(meta) if meta else None

    # --- lifecycle ----------------------------------------------------------------

    def start(self, slug: str, agent: str, kind: str, inputs: dict, query: str,
              *, target: dict | None = None, session_id: str | None = None) -> dict:
        """Launch a run in a background thread and return its meta immediately.

        If an *active* run with the same (slug, agent, query) exists, it is
        returned instead (flagged ``existing``) — re-submitting a query that is
        already being ingested must not race a second copy of the same work.
        """
        builder = GRAPH_BUILDERS.get(agent)
        if builder is None:
            raise KeyError(f"{agent} graph not registered")

        with self._lock:
            self._load_slug(slug)
            for meta in self._runs.values():
                if (meta.get("slug") == slug and meta.get("agent") == agent
                        and meta.get("query") == query and meta["status"] in _ACTIVE
                        and meta.get("session_id") == session_id):
                    return {**meta, "existing": True}

            run_id = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{slugify(query)}"
            if run_id in self._runs:  # same query twice in one second
                run_id += f"-{len(self._runs)}"
            log_dir = storage.agent_log_dir(slug, agent)
            log_dir.mkdir(parents=True, exist_ok=True)
            meta = {
                "run_id": run_id, "slug": slug, "agent": agent, "kind": kind,
                "query": query, "inputs": inputs, "status": "running",
                "target": target, "session_id": session_id,
                "candidates": [], "result_paper_id": None,
                "result_idea_ids": [], "error": None, "note": None,
                "log": f"{run_id}.jsonl",
                "created_at": _now_iso(), "updated_at": _now_iso(),
            }
            self._runs[run_id] = meta
            self._cancels[run_id] = threading.Event()
            self._persist_slug(slug)

        threading.Thread(target=self._execute, args=(run_id,), daemon=True,
                         name=f"run:{run_id}").start()
        return dict(meta)

    def cancel(self, run_id: str) -> dict:
        """Ask a running run to stop after its current step."""
        meta = self.get(run_id)
        if meta is None:
            raise KeyError(run_id)
        if meta["status"] in _ACTIVE:
            self._cancels[run_id].set()
        return meta

    def restart(self, run_id: str) -> dict:
        """Re-run a terminal/paused run with its original inputs (idempotent
        stages skip completed work) and drop the old entry."""
        meta = self.get(run_id)
        if meta is None:
            raise KeyError(run_id)
        if meta["status"] in _ACTIVE:
            raise ValueError("run is still active — cancel it first")
        fresh = self.start(meta["slug"], meta["agent"], meta["kind"],
                           meta["inputs"], meta["query"], target=meta.get("target"),
                           session_id=meta.get("session_id"))
        if fresh["run_id"] != run_id:
            self.delete(run_id)
        return fresh

    def delete(self, run_id: str) -> None:
        """Drop a non-active entry from the registry + index (the ``.jsonl`` run
        log stays on disk as history)."""
        with self._lock:
            meta = self._runs.get(run_id)
            if meta is None:
                return
            if meta["status"] in _ACTIVE:
                raise ValueError("run is still active — cancel it first")
            self._runs.pop(run_id)
            self._cancels.pop(run_id, None)
            self._persist_slug(meta["slug"])

    # --- execution ----------------------------------------------------------------

    def log_path(self, run_id: str) -> Path | None:
        meta = self.get(run_id)
        if meta is None:
            return None
        return storage.agent_log_dir(meta["slug"], meta["agent"]) / meta["log"]

    def _execute(self, run_id: str) -> None:
        meta = self.get(run_id)
        assert meta is not None
        cancel = self._cancels[run_id]
        graph = GRAPH_BUILDERS[meta["agent"]]()
        error: str | None = None
        # The stored inputs are always **JSON** (they round-trip through the run
        # index and a restart). Agents whose graph state isn't JSON-typeable —
        # the creative thinker takes a whole ``ThinkerCall`` — declare an
        # ``input_adapter`` in their manifest, applied here exactly as the dev UI
        # applies it, so an app run and a dev run are the same run.
        adapter = INPUT_ADAPTERS.get(meta["agent"])
        try:
            graph_inputs = adapter(meta["inputs"]) if adapter else meta["inputs"]
        except Exception as e:  # noqa: BLE001 — a bad spec must not kill the thread
            self._update(run_id, status="error", error=f"{type(e).__name__}: {e}")
            return

        with open(self.log_path(run_id), "w") as fh:  # type: ignore[arg-type]
            rec = RunRecorder(fh, meta["agent"], meta["slug"], meta["inputs"])
            rec.start(graph.get_graph(xray=True).draw_mermaid())
            try:
                for ns, mode, data in graph.stream(
                    graph_inputs, stream_mode=["updates", "custom"], subgraphs=True,
                    config={"recursion_limit": GRAPH_RECURSION_LIMIT},
                ):
                    if cancel.is_set():
                        raise _Cancelled()
                    for ev in rec.record(ns, mode, data):
                        self._observe(run_id, ev)
            except _Cancelled:
                error = "cancelled"
            except Exception as e:  # noqa: BLE001 — surfaced in the footer + meta
                error = f"{type(e).__name__}: {e}"
            footer = rec.finish(error=error)

        self._finalize(run_id, footer, error)

    def _observe(self, run_id: str, ev: dict) -> None:
        """Watch the event stream for the paper this run is about. The fetch
        stage's state delta carries ``paper_id`` — the moment it appears, the run
        *attaches* to that paper (the frontend uses this to open its page). A
        duplicate fetch is noted so the UI can say "already in library"."""
        if ev.get("kind") != "node":
            return
        delta = ev.get("result")
        if not isinstance(delta, dict):
            return
        meta = self.get(run_id)
        if meta is None:
            return
        patch: dict[str, Any] = {}
        pid = delta.get("paper_id")
        if isinstance(pid, str) and pid and not meta.get("target"):
            patch["target"] = {"kind": "paper", "id": pid}
            patch["result_paper_id"] = pid
        fetch = delta.get("fetch_result")
        if isinstance(fetch, dict) and fetch.get("status") == "duplicate" and not meta.get("note"):
            patch["note"] = "duplicate"
        if patch:
            self._update(run_id, **patch)

    def _finalize(self, run_id: str, footer: dict, error: str | None) -> None:
        """Translate the run's footer into the entry's terminal/paused status."""
        output = footer.get("output") if isinstance(footer.get("output"), dict) else {}
        patch: dict[str, Any] = {"error": None}
        if error == "cancelled":
            patch["status"] = "cancelled"
        elif error:
            patch.update(status="error", error=error)
        elif output.get("status") == "ambiguous":
            patch.update(status="ambiguous", candidates=output.get("candidates") or [])
        elif output.get("status") == "needs_source":
            patch["status"] = "needs_source"
        else:
            patch["status"] = "done"
        if isinstance(output.get("paper_id"), str) and output["paper_id"]:
            patch["result_paper_id"] = output["paper_id"]
            meta = self.get(run_id)
            if meta is not None and not meta.get("target"):
                patch["target"] = {"kind": "paper", "id": output["paper_id"]}
        if isinstance(output.get("idea_ids"), list):
            patch["result_idea_ids"] = output["idea_ids"]
        # The creative thinker returns one envelope as its whole output. Keeping it
        # on the entry means a reloaded client renders the result from the run
        # index without replaying the event stream to find the footer.
        if "answer" in output and "idea" in output:
            patch["envelope"] = output
        self._update(run_id, **patch)


MANAGER = RunManager()
