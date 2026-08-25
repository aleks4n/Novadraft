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

from dev.registry import (GRAPH_BUILDERS, INPUT_ADAPTERS, LOGGED_AGENTS, POST_RUN,
                          _truthy, list_all)
from dev.run import _log, _run, FIXTURES_DIR, RUNS_DIR, TABLES
from dev.runlog import GraphRunLog, read_run
from dev.eval_paper_pipeline import (
    ensure_papers_fetched, list_workspaces, run_pipeline, write_report,
)
from src.common.schemas.idea_compat import normalize as normalize_idea
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

        # A caller-side step the graph must not do itself (see POST_RUN).
        hook = POST_RUN.get(req.name)
        output = hook(inputs, rl.output) if hook and not rl.error else rl.output

        record = {
            "name": req.name, "kind": req.kind, "inputs": inputs,
            "timestamp": datetime.now().isoformat(),
            "output": output, "error": rl.error,
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


# --- ideas: the idea graph and the thinking tree -----------------------------
# Two different graphs, deliberately not merged: the **idea graph** is what was kept
# (accepted ideas + typed edges), the **thinking tree** inside each idea's runs[] is how
# it was found. Both are read-only here — the viewer never writes an idea.

def _load_idea(slug: str, idea_id: str) -> dict | None:
    """One idea record, normalized. Phase-1 records are adapted on read (they have no
    runs[]); ``slug == "_fixtures"`` reads ``dev/fixtures/<idea_id>.json`` instead, so a
    fixture idea and a workspace idea render through the same path."""
    if slug == "_fixtures":
        path = FIXTURES_DIR / f"{idea_id}.json"
        raw = json.loads(path.read_text()) if path.exists() else None
    else:
        raw = storage.read_idea_json(slug, idea_id)
    if raw is None:
        return None
    return normalize_idea(raw).model_dump(mode="json")


def _fixture_idea_names() -> list[str]:
    """Fixture files that are idea records (as opposed to agent-input fixtures)."""
    names = []
    for path in sorted(FIXTURES_DIR.glob("*.json")):
        try:
            raw = json.loads(path.read_text())
        except Exception:  # noqa: BLE001 — a malformed fixture shouldn't kill the list
            continue
        if isinstance(raw, dict) and "idea_id" in raw:
            names.append(path.stem)
    return names


@app.get("/api/ideas")
def api_ideas():
    """Every idea the harness can show, by source, with how much thinking each carries."""
    sources: list[dict] = []
    for slug in (w["category_slug"] for w in list_workspaces()):
        for idea_id in storage.list_idea_ids(slug):
            record = _load_idea(slug, idea_id)
            if record is None:
                continue
            sources.append(_idea_summary(slug, idea_id, record))
    for name in _fixture_idea_names():
        record = _load_idea("_fixtures", name)
        if record is not None:
            sources.append(_idea_summary("_fixtures", name, record))
    return {"ideas": sources}


def _idea_summary(slug: str, ident: str, record: dict) -> dict:
    runs = record.get("runs") or []
    return {
        "workspace": slug,
        "ident": ident,
        "idea_id": record.get("idea_id"),
        "title": record.get("title") or ident,
        "state": record.get("state"),
        "runs": len(runs),
        "nodes": sum(len(r.get("nodes") or []) for r in runs),
    }


@app.get("/api/ideas/{slug}/{idea_id}")
def api_idea(slug: str, idea_id: str):
    record = _load_idea(slug, idea_id)
    if record is None:
        raise HTTPException(404, "idea not found")
    return record


class ApplyRequest(BaseModel):
    """A demo stand-in for the user's decision, not for the orchestrator.

    The dev UI shows a finished envelope and lets you tick which drafts and which
    suggestions to keep; this carries that choice. The planner/executor/reflector loop
    that would normally make the call does not exist yet — what runs underneath is only
    the apply step, the same one an orchestrator would use.
    """

    workspace: str
    envelope: dict
    accept_drafts: list[int] = []
    accept_operations: list[int] = []
    persist_idea: bool = True


@app.post("/api/idea/apply")
def api_idea_apply(req: ApplyRequest):
    """Write the ticked parts of an envelope into the workspace.

    Nothing here decides anything: every id that gets created was ticked by a human in
    the panel, and everything left unticked is recorded as rejected on the run that
    proposed it rather than dropped.
    """
    from src.agents.creative_thinker.schemas import Envelope
    from src.agents.idea_agent import apply_envelope

    try:
        envelope = Envelope.model_validate(req.envelope)
    except Exception as exc:  # noqa: BLE001 — a malformed envelope is a user error here
        raise HTTPException(400, f"not a result envelope: {exc}") from exc

    return apply_envelope(
        req.workspace, envelope,
        accept_drafts=req.accept_drafts,
        accept_operations=req.accept_operations,
        persist_idea=req.persist_idea,
    )


@app.get("/api/idea-graph/{slug}")
def api_idea_graph(slug: str):
    """The idea graph for one workspace: `gap` and `idea` nodes joined by `addresses`
    and the typed idea-to-idea edges. **Accepted ideas only** — no reasoning node ever
    appears here; that separation is the point of the two-graph split."""
    if slug == "_fixtures":
        records = [(name, _load_idea("_fixtures", name)) for name in _fixture_idea_names()]
    else:
        records = [(i, _load_idea(slug, i)) for i in storage.list_idea_ids(slug)]

    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    for ident, record in records:
        if record is None:
            continue
        idea_key = record.get("idea_id") or ident
        nodes[idea_key] = {"id": idea_key, "type": "idea", "ident": ident,
                           "workspace": slug, "label": record.get("title") or ident,
                           "state": record.get("state"),
                           "runs": len(record.get("runs") or [])}
        for gap_id in record.get("addresses") or []:
            nodes.setdefault(gap_id, {"id": gap_id, "type": "gap", "label": gap_id})
            edges.append({"from": idea_key, "to": gap_id, "type": "addresses",
                          "rationale": ""})
        for relation in record.get("relations") or []:
            edges.append({"from": idea_key, "to": relation.get("target"),
                          "type": relation.get("type"),
                          "rationale": relation.get("rationale") or "",
                          "run": relation.get("run")})
    return {"nodes": list(nodes.values()), "edges": edges}


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
