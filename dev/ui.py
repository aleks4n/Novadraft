"""Minimal web UI for the dev harness.

Run:
    cd code/novadraft/dev
    python ui.py
    # then open http://127.0.0.1:8000

Exposes the same agents/flows as run.py and writes to the same dev/runs/ log dir.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _env_bootstrap  # noqa: F401,E402

import json  # noqa: E402

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.responses import FileResponse
from pydantic import BaseModel

from registry import AGENTS, FLOWS, list_all
from run import _log, _run, FIXTURES_DIR, RUNS_DIR

HERE = Path(__file__).resolve().parent
app = FastAPI(title="novadraft dev")


class RunRequest(BaseModel):
    kind: str  # "agent" | "flow"
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
    if req.kind not in ("agent", "flow"):
        raise HTTPException(400, "kind must be 'agent' or 'flow'")
    table = AGENTS if req.kind == "agent" else FLOWS
    if req.name not in table:
        raise HTTPException(404, f"unknown {req.kind}: {req.name}")
    record = _run(req.kind, req.name, req.inputs)
    path = _log(record, req.name)
    record["log_file"] = path.name
    return record


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
