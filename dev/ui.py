"""Minimal web UI for the dev harness.

Run:
    cd code/novadraft/dev
    python ui.py
    # then open http://127.0.0.1:8000

Exposes the same agents/subagents/flows as run.py and writes to the same
dev/runs/ log dir.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _env_bootstrap  # noqa: F401,E402

import json  # noqa: E402
import queue  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from dev.registry import list_all, LOGGED_AGENTS
from dev.run import _log, _run, FIXTURES_DIR, RUNS_DIR, TABLES
from dev.eval_paper_pipeline import (
    ensure_papers_fetched, list_workspaces, run_pipeline, write_report,
)
from src.common.utils.runlog import log_path_for

# How often the dev UI polls a running agent's on-disk JSON log for
# progress, in seconds. See planning/technical-discussions/LOGGING_SYSTEM.md
# — the log file is the single source of truth; the UI just tails it.
_POLL_INTERVAL_S = 0.7

HERE = Path(__file__).resolve().parent
app = FastAPI(title="novadraft dev")


class RunRequest(BaseModel):
    kind: str  # "agent" | "subagent" | "flow"
    name: str
    inputs: dict


class EvalRequest(BaseModel):
    mode: str  # "summary" | "gap"
    category_slug: str
    label: str = "web"
    papers: list[str] | None = None  # existing paper_ids to evaluate
    queries: list[str] | None = None  # paper names/IDs/URLs to fetch+extract first


@app.get("/")
def index():
    return FileResponse(HERE / "static" / "index.html")


@app.get("/api/list")
def api_list():
    fixtures = sorted(p.name for p in FIXTURES_DIR.glob("*.json"))
    return {**list_all(), "fixtures": fixtures}


@app.get("/api/fixture/{name}")
def api_fixture(name: str):
    path = FIXTURES_DIR / name
    if not path.exists():
        raise HTTPException(404, "fixture not found")
    return json.loads(path.read_text())


@app.post("/api/run")
def api_run(req: RunRequest):
    table = TABLES.get(req.kind)
    if table is None:
        raise HTTPException(400, f"kind must be one of {sorted(TABLES)}")
    if req.name not in table:
        raise HTTPException(404, f"unknown {req.kind}: {req.name}")
    record = _run(req.kind, req.name, req.inputs)
    path = _log(record, req.name)
    record["log_file"] = path.name
    return record


@app.post("/api/run/stream")
def api_run_stream(req: RunRequest):
    """Like /api/run, but streams progress (SSE) as the agent runs, then a
    final ``done`` event with the full record.

    For agents in ``registry.LOGGED_AGENTS`` (agents that call
    ``runlog.start_run`` internally — see ``src/common/utils/runlog.py``),
    progress comes from polling the run's on-disk JSON log: that file *is*
    the live progress feed, so each ``log`` event simply carries its full
    current content. Other kinds just run and emit a single ``done`` event.
    """
    table = TABLES.get(req.kind)
    if table is None:
        raise HTTPException(400, f"kind must be one of {sorted(TABLES)}")
    if req.name not in table:
        raise HTTPException(404, f"unknown {req.kind}: {req.name}")

    workspace_field = LOGGED_AGENTS.get(req.name) if req.kind == "agent" else None
    workspace = req.inputs.get(workspace_field) if workspace_field else None

    def generate():
        events: queue.Queue = queue.Queue()

        def worker():
            record = _run(req.kind, req.name, req.inputs)
            events.put({"type": "done", "record": record})

        if not workspace:
            threading.Thread(target=worker, daemon=True).start()
            while True:
                event = events.get()
                if event["type"] == "done":
                    path = _log(event["record"], req.name)
                    event["record"]["log_file"] = path.name
                yield f"data: {json.dumps(event, default=str)}\n\n"
                if event["type"] == "done":
                    break
            return

        log_dir = log_path_for(workspace, req.name)
        before = set(p.name for p in log_dir.glob("*.json")) if log_dir.exists() else set()

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

        active_path = None
        last_payload = None
        while thread.is_alive() or active_path is not None:
            if active_path is None and log_dir.exists():
                new_files = sorted(set(p.name for p in log_dir.glob("*.json")) - before)
                if new_files:
                    active_path = log_dir / new_files[-1]

            if active_path is not None and active_path.exists():
                try:
                    payload = json.loads(active_path.read_text())
                except (OSError, json.JSONDecodeError):
                    payload = None
                if payload is not None and payload != last_payload:
                    last_payload = payload
                    yield f"data: {json.dumps({'type': 'log', 'log': payload}, default=str)}\n\n"

            if not thread.is_alive():
                break
            time.sleep(_POLL_INTERVAL_S)

        # The worker may have already finished by the time we got our first
        # chance to poll (e.g. every step was skipped, no LLM calls) — look
        # for the new file once more now that the thread is guaranteed done,
        # so fast/fully-cached runs still get their log rendered.
        if active_path is None and log_dir.exists():
            new_files = sorted(set(p.name for p in log_dir.glob("*.json")) - before)
            if new_files:
                active_path = log_dir / new_files[-1]

        # Final read, in case the worker finished between our last poll and now.
        if active_path is not None and active_path.exists():
            try:
                payload = json.loads(active_path.read_text())
                if payload != last_payload:
                    yield f"data: {json.dumps({'type': 'log', 'log': payload}, default=str)}\n\n"
            except (OSError, json.JSONDecodeError):
                pass

        event = events.get()
        if active_path is not None:
            event["record"]["run_log"] = active_path.name
        # Persist now that run_log is attached, so reopening this run later
        # (via /api/runs/{name}) can still find its step-tree log file —
        # persisting earlier (before run_log was known) would silently drop it.
        path = _log(event["record"], req.name)
        event["record"]["log_file"] = path.name
        yield f"data: {json.dumps(event, default=str)}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


@app.get("/api/workspaces")
def api_workspaces():
    return {"workspaces": list_workspaces()}


@app.get("/api/workspaces/{slug}/logs/{agent}")
def api_workspace_agent_logs(slug: str, agent: str):
    """Past runs of a logged agent for one workspace — see LOGGED_AGENTS.

    The run log files (workspace/<slug>/logs/<agent>/*.json) are the same
    files /api/run/stream tails live, so this is also how the dev UI lets
    you reopen a finished run.
    """
    log_dir = log_path_for(slug, agent)
    runs = sorted((p.name for p in log_dir.glob("*.json")), reverse=True) if log_dir.exists() else []
    return {"runs": runs}


@app.get("/api/workspaces/{slug}/logs/{agent}/{run_id}")
def api_workspace_agent_log(slug: str, agent: str, run_id: str):
    path = log_path_for(slug, agent) / run_id
    if not path.exists():
        raise HTTPException(404, "run log not found")
    return json.loads(path.read_text())


@app.get("/api/workspaces/{slug}/papers")
def api_workspace_papers(slug: str):
    from dev.eval_paper_pipeline import _fully_extracted_papers
    return {"paper_ids": _fully_extracted_papers(slug)}


@app.post("/api/eval/stream")
def api_eval_stream(req: EvalRequest):
    """Run the summary-agent or gap-agent eval pipeline over a workspace's
    extracted papers, streaming per-paper progress (SSE), then a final
    ``done`` event with the markdown report and raw JSON results.
    """
    if req.mode not in ("summary", "gap"):
        raise HTTPException(400, "mode must be 'summary' or 'gap'")

    def generate():
        events: queue.Queue = queue.Queue()

        def on_progress(event: dict) -> None:
            events.put({"type": "step", **event})

        def worker():
            try:
                paper_ids = req.papers
                issues: list[dict] = []
                if req.queries:
                    paper_ids, issues = ensure_papers_fetched(
                        req.category_slug, req.queries, on_progress=on_progress
                    )
                results = run_pipeline(
                    req.category_slug, req.mode, paper_ids, on_progress=on_progress
                )
                run_dir = write_report(req.label, req.category_slug, req.mode, results)
                report_md = (run_dir / "report.md").read_text()
                events.put({
                    "type": "done",
                    "report_md": report_md,
                    "raw": results,
                    "run_dir": run_dir.name,
                    "fetch_issues": issues,
                })
            except Exception as e:  # noqa: BLE001
                events.put({"type": "error", "error": str(e)})

        threading.Thread(target=worker, daemon=True).start()

        while True:
            event = events.get()
            yield f"data: {json.dumps(event, default=str)}\n\n"
            if event["type"] in ("done", "error"):
                break

    return StreamingResponse(generate(), media_type="text/event-stream")


@app.get("/api/runs")
def api_runs():
    files = sorted((p.name for p in RUNS_DIR.glob("*.json")), reverse=True)
    return {"runs": files}


@app.get("/api/runs/{name}")
def api_run_get(name: str):
    path = RUNS_DIR / name
    if not path.exists():
        raise HTTPException(404, "run not found")
    return json.loads(path.read_text())


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
