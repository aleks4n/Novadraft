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
from datetime import datetime  # noqa: E402

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from dev.registry import GRAPH_BUILDERS, INPUT_ADAPTERS, LOGGED_AGENTS, _truthy, list_all
from dev.run import _log, _run, FIXTURES_DIR, RUNS_DIR, TABLES
from dev.runlog import GraphRunLog, read_run
from dev.eval_paper_pipeline import (
    ensure_papers_fetched, list_workspaces, run_pipeline, write_report,
)
from src.common.utils import storage

HERE = Path(__file__).resolve().parent
app = FastAPI(title="novadraft dev")

# Generous super-step ceiling for streamed graph runs. The agents bound their
# own loops in their routers (attempt >= max_loops), so this is just a backstop
# big enough to cover a top-level pipeline plus its bounded subgraph cycles.
GRAPH_RECURSION_LIMIT = 80


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


def _sse(obj: dict) -> str:
    return f"data: {json.dumps(obj, default=str)}\n\n"


def _sse_type(ev: dict) -> str:
    """Map a recorded event's ``kind`` to the SSE envelope type the frontend
    expects: ``run_start`` / ``run_end`` pass through, everything else (node /
    llm / tool / event leaves) is a plain ``event``."""
    kind = ev.get("kind")
    return kind if kind in ("run_start", "run_end") else "event"


@app.post("/api/run/stream")
def api_run_stream(req: RunRequest):
    """Like /api/run, but streams progress (SSE) then a final ``done`` event.

    For graph agents (``registry.GRAPH_BUILDERS``) progress comes straight from
    the LangGraph stream: we run
    ``graph.stream(stream_mode=["updates","custom"], subgraphs=True)`` and feed
    each chunk to a :class:`dev.runlog.RunRecorder`, which appends one flat JSON
    line per event to ``workspace/<slug>/logs/<agent>/<run>.jsonl`` *and* returns
    it to forward over SSE — so the file being written is exactly what's being
    streamed. The stream opens with a ``run_start`` event carrying the agent's
    Mermaid graph (``get_graph(xray=True)``, subgraphs expanded), then one event
    per node/llm/tool/note, then ``run_end``. Other kinds just run and emit a
    single ``done`` event.
    """
    table = TABLES.get(req.kind)
    if table is None:
        raise HTTPException(400, f"kind must be one of {sorted(TABLES)}")
    if req.name not in table:
        raise HTTPException(404, f"unknown {req.kind}: {req.name}")

    builder = GRAPH_BUILDERS.get(req.name) if req.kind == "agent" else None

    if builder is None:
        # Non-streaming kinds (subagents, flows): run to completion, one event.
        def generate_simple():
            record = _run(req.kind, req.name, req.inputs)
            path = _log(record, req.name)
            record["log_file"] = path.name
            yield _sse({"type": "done", "record": record})
        return StreamingResponse(generate_simple(), media_type="text/event-stream")

    workspace_field = LOGGED_AGENTS.get(req.name)
    workspace = req.inputs.get(workspace_field) if workspace_field else None

    def generate():
        inputs = dict(req.inputs)
        if "force" in inputs:
            inputs["force"] = _truthy(inputs["force"])

        # Some graph agents take state that isn't directly form-typeable (e.g.
        # the validator's ``Gap`` object). Their manifest supplies an adapter
        # that turns the raw form inputs into graph state; the raw ``inputs``
        # are still what we log/slug/display.
        adapter = INPUT_ADAPTERS.get(req.name)
        graph_inputs = adapter(inputs) if adapter else inputs

        graph = builder()
        hint = next((v for v in inputs.values() if isinstance(v, str)), "")
        # Same stream→record→file engine the agents and app backend use. With no
        # workspace slug, fall back to the harness' shared graph_logs dir.
        rl = GraphRunLog(
            req.name, workspace=workspace,
            log_dir=None if workspace else RUNS_DIR / "graph_logs",
            run_hint=hint, recursion_limit=GRAPH_RECURSION_LIMIT,
        )

        t0 = time.time()
        for ev in rl.stream(graph, graph_inputs, log_inputs=inputs):
            yield _sse({"type": _sse_type(ev), "event": ev})

        record = {
            "name": req.name, "kind": req.kind, "inputs": inputs,
            "timestamp": datetime.now().isoformat(),
            "output": rl.output, "error": rl.error,
            "duration_s": round(time.time() - t0, 3),
            "run_log": rl.log_path.name,
        }
        path = _log(record, req.name)
        record["log_file"] = path.name
        yield _sse({"type": "done", "record": record})

    return StreamingResponse(generate(), media_type="text/event-stream")


@app.get("/api/graph/{agent}")
def api_graph(agent: str):
    """The agent's graph as Mermaid (``get_graph(xray=True)``, subgraphs
    expanded inline). Lets the UI render the pipeline before a run starts."""
    builder = GRAPH_BUILDERS.get(agent)
    if builder is None:
        raise HTTPException(404, f"not a graph agent: {agent}")
    return {"mermaid": builder().get_graph(xray=True).draw_mermaid()}


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
    log_dir = storage.agent_log_dir(slug, agent)
    runs = sorted((p.name for p in log_dir.glob("*.jsonl")), reverse=True) if log_dir.exists() else []
    return {"runs": runs}


@app.get("/api/workspaces/{slug}/logs/{agent}/{run_id}")
def api_workspace_agent_log(slug: str, agent: str, run_id: str):
    """One past run, read back from its ``.jsonl`` into
    ``{header, events, footer}`` — the same shape the live stream produces, so a
    reopened run renders identically (see ``dev/runlog.read_run``)."""
    path = storage.agent_log_dir(slug, agent) / run_id
    if not path.exists():
        raise HTTPException(404, "run log not found")
    return read_run(path)


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
