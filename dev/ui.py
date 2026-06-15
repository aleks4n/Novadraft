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

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from dev.registry import list_all
from dev.run import _log, _run, FIXTURES_DIR, RUNS_DIR, TABLES

HERE = Path(__file__).resolve().parent
app = FastAPI(title="novadraft dev")


class RunRequest(BaseModel):
    kind: str  # "agent" | "subagent" | "flow"
    name: str
    inputs: dict


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
    """Like /api/run, but streams ``on_step`` progress events (SSE) as the
    agent runs, then a final ``done`` event with the full record.

    Only agents that accept an ``on_step`` callback (see
    ``registry.AGENT_STEPS``) will emit progress events; others just emit a
    single ``done`` event once finished.
    """
    table = TABLES.get(req.kind)
    if table is None:
        raise HTTPException(400, f"kind must be one of {sorted(TABLES)}")
    if req.name not in table:
        raise HTTPException(404, f"unknown {req.kind}: {req.name}")

    def generate():
        events: queue.Queue = queue.Queue()

        def on_step(event: dict) -> None:
            events.put({"type": "step", **event})

        def worker():
            record = _run(req.kind, req.name, req.inputs, on_step=on_step)
            path = _log(record, req.name)
            record["log_file"] = path.name
            events.put({"type": "done", "record": record})

        threading.Thread(target=worker, daemon=True).start()

        while True:
            event = events.get()
            yield f"data: {json.dumps(event, default=str)}\n\n"
            if event["type"] == "done":
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
