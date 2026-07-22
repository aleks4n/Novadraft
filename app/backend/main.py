"""Ideation UI backend — a thin product API over the novadraft workspace.

Maps the persistent knowledge base (``workspace/<slug>/...``) onto the data model
the Research Ideation v2 frontend expects, and exposes the **paper agent** as a
live, streamed run. It deliberately reuses the dev-harness internals rather than
reimplementing them:

  - ``dev.registry.GRAPH_BUILDERS["paper_agent"]`` — the compiled LangGraph.
  - ``dev.runlog.RunRecorder`` — turns the graph stream into the same flat
    ``.jsonl`` events the dev UI renders (so a run streams + persists in one pass).
  - ``src.common.utils.storage`` — the workspace filesystem layout.

Run it from the novadraft root::

    cd code/novadraft
    python -m uvicorn app.backend.main:app --reload --port 8787

``_env_bootstrap`` re-execs into the project's declared interpreter, so the agent
(langgraph + marker-pdf + litellm) runs in the right env regardless of how it was
launched.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Make the novadraft package root importable (dev.*, src.*) and hop into the
# declared interpreter — same prelude the dev harness uses.
_NOVADRAFT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_NOVADRAFT_ROOT))
import _env_bootstrap  # noqa: F401,E402

import asyncio  # noqa: E402
import io  # noqa: E402
import json  # noqa: E402
import tarfile  # noqa: E402
import zipfile  # noqa: E402

from fastapi import FastAPI, File, HTTPException, UploadFile  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import StreamingResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from dev.registry import _truthy  # noqa: E402
from dev.eval_paper_pipeline import _fully_extracted_papers, list_workspaces  # noqa: E402
from src.common.utils import storage  # noqa: E402
from src.agents.ranking_agent.schemas import RankingConfig  # noqa: E402

from app.backend import mappers  # noqa: E402
from app.backend.runs import MANAGER  # noqa: E402

app = FastAPI(title="novadraft · ideation ui")

# The Vite dev server (web app) and the Tauri webview load the frontend from a
# different origin than this API, so allow cross-origin during development.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- workspace helpers -------------------------------------------------------

def _require_meta(slug: str) -> dict:
    meta_path = storage.category_dir(slug) / "meta.json"
    if not meta_path.exists():
        raise HTTPException(404, f"topic '{slug}' not found")
    meta = json.loads(meta_path.read_text())
    # The directory name is authoritative — meta.json's internal ``category_slug``
    # can be stale (fixtures copied between workspaces), so the id always comes
    # from the folder the file lives in.
    meta["category_slug"] = slug
    return meta


def _read_papers(slug: str, *, full: bool = False) -> list[dict]:
    pdir = storage.papers_dir(slug)
    if not pdir.exists():
        return []
    out = []
    for d in sorted(pdir.iterdir()):
        rec = storage.read_paper_json(slug, d.name)
        if rec:
            out.append(mappers.map_paper(rec, full=full))
    return out


def _read_gaps(slug: str, bucket: str = "pending") -> list[dict]:
    """Gaps from ``gaps/<bucket>/`` — e.g. ``pending`` (freshly mined, per-paper)
    or ``master`` (the validated, deduplicated list). Each gap is tagged with its
    stage so the UI can show where it is in the ingestion pipeline."""
    gdir = storage.category_dir(slug) / "gaps" / bucket
    if not gdir.exists():
        return []
    out = []
    for f in sorted(gdir.glob("*.json")):
        try:
            out.append(mappers.map_gap(json.loads(f.read_text()), stage=bucket))
        except (json.JSONDecodeError, OSError):
            continue
    return out


def _read_all_gaps(slug: str) -> list[dict]:
    """Every gap across every stage folder, each tagged with its stage. A gap
    lives in exactly one stage at a time, so this is the full per-paper picture
    (mined → deduped → validated → ranked, plus archived duplicates)."""
    out = []
    for stage in storage.GAP_STAGES:
        out.extend(_read_gaps(slug, stage))
    return out


def _read_ideas(slug: str) -> list[dict]:
    idir = storage.ideas_dir(slug)
    if not idir.exists():
        return []
    out = []
    for f in sorted(idir.glob("*.json")):
        try:
            out.append(mappers.map_idea(json.loads(f.read_text())))
        except (json.JSONDecodeError, OSError):
            continue
    return out


def _counts(slug: str) -> tuple[int, int, int]:
    pdir = storage.papers_dir(slug)
    n_papers = sum(1 for d in pdir.iterdir() if (d / "paper.json").exists()) if pdir.exists() else 0
    # Every live gap (across stages, minus archived duplicates) — so the count is
    # stable as gaps flow pending → to_validate → to_rank → master.
    n_gaps = sum(len(storage.list_gap_ids(slug, s))
                 for s in storage.GAP_STAGES if s != "archive")
    n_ideas = len(storage.list_idea_ids(slug))
    return n_papers, n_gaps, n_ideas


# --- read endpoints ----------------------------------------------------------

@app.get("/api/topics")
def api_topics():
    """All workspaces as design topics (meta + paper/gap counts)."""
    topics = []
    for ws in list_workspaces():
        slug = ws["category_slug"]
        meta = _require_meta(slug)
        n_papers, n_gaps, n_ideas = _counts(slug)
        topics.append(mappers.map_topic(meta, n_papers=n_papers, n_gaps=n_gaps, n_ideas=n_ideas))
    return {"topics": topics}


class TopicCreateRequest(BaseModel):
    topic: str


@app.post("/api/topics")
def api_create_topic(req: TopicCreateRequest):
    """Bootstrap a new research category from a free-text topic via the category
    agent: it looks the topic up on Wikipedia, takes a short description, and
    writes ``workspace/<slug>/meta.json`` — the record every other endpoint
    requires. Returns the created topic (same shape as ``/api/topics``), so the
    frontend can switch to it immediately.

    Runs synchronously (one Wikipedia lookup, ~1s); FastAPI serves it from the
    threadpool, so the event loop is not blocked. Re-creating an existing topic
    refreshes its name/description rather than erroring.
    """
    topic = (req.topic or "").strip()
    if not topic:
        raise HTTPException(400, "topic must not be empty")

    # Imported here (not at module top) so an import-time hiccup in the agent
    # can't take down the read endpoints the rest of the app depends on.
    from src.agents.category_agent import run as run_category

    try:
        result = run_category(topic, stream=True, echo=False)
    except Exception as e:  # noqa: BLE001 — surface the failure to the client
        raise HTTPException(502, f"category agent failed: {type(e).__name__}: {e}")

    slug = result["category_slug"]
    meta = _require_meta(slug)
    n_papers, n_gaps, n_ideas = _counts(slug)
    return mappers.map_topic(meta, n_papers=n_papers, n_gaps=n_gaps, n_ideas=n_ideas)


@app.get("/api/topics/{slug}")
def api_topic(slug: str):
    """One topic with its papers and gaps inlined (the shape the sidebar wants)."""
    meta = _require_meta(slug)
    papers = _read_papers(slug)
    gaps = _read_gaps(slug)
    master_gaps = _read_gaps(slug, "master")
    all_gaps = _read_all_gaps(slug)
    ideas = _read_ideas(slug)
    topic = mappers.map_topic(meta, n_papers=len(papers), n_gaps=len(gaps), n_ideas=len(ideas))
    return {"meta": topic, "papers": papers, "gaps": gaps,
            "masterGaps": master_gaps, "allGaps": all_gaps, "ideas": ideas,
            "rankingConfig": mappers.map_ranking_config(RankingConfig())}


@app.get("/api/topics/{slug}/papers")
def api_papers(slug: str):
    _require_meta(slug)
    return {"papers": _read_papers(slug)}


@app.get("/api/topics/{slug}/papers/{paper_id}")
def api_paper(slug: str, paper_id: str):
    rec = storage.read_paper_json(slug, paper_id)
    if not rec:
        raise HTTPException(404, f"paper '{paper_id}' not found in '{slug}'")
    return mappers.map_paper(rec, full=True)


@app.get("/api/topics/{slug}/gaps")
def api_gaps(slug: str):
    _require_meta(slug)
    return {"gaps": _read_gaps(slug)}


@app.get("/api/topics/{slug}/ideas")
def api_ideas(slug: str):
    _require_meta(slug)
    return {"ideas": _read_ideas(slug)}


@app.get("/api/topics/{slug}/ideas/{idea_id}")
def api_idea(slug: str, idea_id: str):
    rec = storage.read_idea_json(slug, idea_id)
    if not rec:
        raise HTTPException(404, f"idea '{idea_id}' not found in '{slug}'")
    return mappers.map_idea(rec)


# --- agent runs (server-tracked; see app/backend/runs.py) ---------------------
# Runs execute in manager threads, detached from any HTTP connection: starting a
# run returns its meta immediately, progress is observed via a replay-and-follow
# SSE stream, and the run survives page reloads / navigation / dropped clients.

class PaperRunRequest(BaseModel):
    query: str
    force: bool = False


class IdeaRunRequest(BaseModel):
    mode: str = "generate"  # "generate" (from gaps) | "expand" (a user sketch)
    sketch: str = ""
    count: int = 3


def _sse(obj: dict) -> str:
    return f"data: {json.dumps(obj, default=str)}\n\n"


@app.get("/api/topics/{slug}/runs")
def api_runs(slug: str):
    """Every tracked run of this workspace (active, paused, and recent terminal
    ones) — what the frontend restores its queue/transcripts from after a reload."""
    _require_meta(slug)
    return {"runs": MANAGER.list(slug)}


@app.post("/api/topics/{slug}/papers/run")
def api_paper_run(slug: str, req: PaperRunRequest):
    """Start ingesting ``query`` through the full pipeline (paper agent → director
    → validator → ranking) in a detached run. Returns the run meta immediately;
    an identical query already in flight is returned instead of duplicated."""
    _require_meta(slug)
    inputs = {"category_slug": slug, "query": req.query, "force": _truthy(req.force)}
    return MANAGER.start(slug, "pipeline", "paper", inputs, req.query)


@app.post("/api/topics/{slug}/gaps/{gap_id}/resume")
def api_gap_resume(slug: str, gap_id: str):
    """Resume ingestion for one gap (director → validator) from where it stopped.
    Uses the gap's stage folder as the checkpoint — a no-op if it's already done."""
    _require_meta(slug)
    return MANAGER.start(slug, "resume", "resume",
                         {"category_slug": slug, "gap_id": gap_id}, f"Resume {gap_id}",
                         target={"kind": "gap", "id": gap_id})


@app.post("/api/topics/{slug}/papers/{paper_id}/resume-gaps")
def api_paper_gaps_resume(slug: str, paper_id: str):
    """Resume gap ingestion (director → validator) for all of a paper's gaps that
    haven't finished — those still in pending/ (need dedup) or to_validate/."""
    _require_meta(slug)
    return MANAGER.start(slug, "resume", "resume",
                         {"category_slug": slug, "paper_id": paper_id},
                         f"Resume gaps · {paper_id}", target={"kind": "paper", "id": paper_id})


@app.get("/api/runs/{run_id}/stream")
async def api_run_stream(run_id: str, since: int = 0):
    """Replay a run's recorded events from ``since`` and follow the log live
    until the run reaches a terminal/paused state. Reconnect-safe: the ``.jsonl``
    is the source of truth, so a reloaded client re-attaches with full history."""
    meta = MANAGER.get(run_id)
    if meta is None:
        raise HTTPException(404, f"unknown run '{run_id}'")
    path = MANAGER.log_path(run_id)

    async def generate():
        pos = 0
        buf = ""
        while True:
            m = MANAGER.get(run_id)
            if path is not None and path.exists():
                with open(path) as f:
                    f.seek(pos)
                    buf += f.read()
                    pos = f.tell()
                # Emit only complete lines; a partially-flushed line waits.
                while "\n" in buf:
                    line, buf = buf.split("\n", 1)
                    if not line.strip():
                        continue
                    ev = json.loads(line)
                    if ev.get("seq", 0) < since:
                        continue
                    kind = ev.get("kind")
                    typ = "run_start" if kind == "run_start" else "run_end" if kind == "run_end" else "event"
                    yield _sse({"type": typ, "event": ev})
                    if kind == "run_end":
                        yield _sse({"type": "meta", "run": MANAGER.get(run_id)})
                        return
            if m is None or m["status"] != "running":
                # Interrupted runs have no footer — close with the meta we have.
                yield _sse({"type": "meta", "run": m})
                return
            await asyncio.sleep(0.3)

    return StreamingResponse(generate(), media_type="text/event-stream")


@app.post("/api/runs/{run_id}/cancel")
def api_run_cancel(run_id: str):
    """Stop a running run after its current step (resumable via restart)."""
    try:
        return MANAGER.cancel(run_id)
    except KeyError:
        raise HTTPException(404, f"unknown run '{run_id}'")


@app.post("/api/runs/{run_id}/restart")
def api_run_restart(run_id: str):
    """Re-run a cancelled/interrupted/failed run with its original inputs —
    completed stages are skipped (stage folders are the checkpoint)."""
    try:
        return MANAGER.restart(run_id)
    except KeyError:
        raise HTTPException(404, f"unknown run '{run_id}'")
    except ValueError as e:
        raise HTTPException(409, str(e))


@app.delete("/api/runs/{run_id}")
def api_run_delete(run_id: str):
    """Drop a non-active run from the queue (its log file stays on disk)."""
    try:
        MANAGER.delete(run_id)
    except ValueError as e:
        raise HTTPException(409, str(e))
    return {"ok": True}


_TEX_SOURCE_DIR = "source"  # paper-relative dir the extract step reads tex from


def _guess_main_tex(tex_dir: Path) -> Path | None:
    """The .tex file most likely to be the document root (has \\documentclass)."""
    candidates = list(tex_dir.rglob("*.tex"))
    if not candidates:
        return None
    for p in candidates:
        try:
            if r"\documentclass" in p.read_text(errors="ignore")[:4000]:
                return p
        except OSError:
            continue
    return candidates[0]


def _install_pdf(pdir: Path, data: bytes, rec: dict) -> None:
    (pdir / "source.pdf").write_bytes(data)
    rec.setdefault("source", {}).setdefault("files", {})["pdf"] = "source.pdf"


def _install_tex_archive(pdir: Path, data: bytes, filename: str, rec: dict) -> None:
    tex_dir = pdir / _TEX_SOURCE_DIR
    name = filename.lower()
    try:
        if name.endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                zf.extractall(tex_dir)
        else:  # .tar.gz / .tgz / .tar
            with tarfile.open(fileobj=io.BytesIO(data)) as tf:
                tf.extractall(tex_dir, filter="data")
    except (zipfile.BadZipFile, tarfile.TarError, OSError) as e:
        raise HTTPException(400, f"could not read the archive: {e}")

    main = _guess_main_tex(tex_dir)
    if main is None:
        raise HTTPException(400, "archive contains no .tex file")
    files = rec.setdefault("source", {}).setdefault("files", {})
    files["tex_dir"] = _TEX_SOURCE_DIR
    files["tex_main"] = f"{_TEX_SOURCE_DIR}/{main.relative_to(tex_dir).as_posix()}"


@app.post("/api/topics/{slug}/papers/{paper_id}/source")
async def api_upload_source(slug: str, paper_id: str, file: UploadFile = File(...)):
    """Attach a user-supplied full-text source to a paper that fetched without one.

    Accepts a PDF or a LaTeX source archive (.zip / .tar.gz); writes it into the
    paper's folder and records it in ``source.files`` so a subsequent paper-agent
    run resumes the (idempotent) extract → summarize → gap stages. Returns the
    updated paper so the caller can re-render before kicking off the resume."""
    _require_meta(slug)
    rec = storage.read_paper_json(slug, paper_id)
    if not rec:
        raise HTTPException(404, f"paper '{paper_id}' not found in '{slug}'")

    name = (file.filename or "").lower()
    data = await file.read()
    if not data:
        raise HTTPException(400, "uploaded file is empty")

    pdir = storage.paper_dir(slug, paper_id)
    pdir.mkdir(parents=True, exist_ok=True)
    if name.endswith(".pdf"):
        _install_pdf(pdir, data, rec)
    elif name.endswith((".zip", ".tar.gz", ".tgz", ".tar")):
        _install_tex_archive(pdir, data, name, rec)
    else:
        raise HTTPException(400, "expected a .pdf or a LaTeX source archive (.zip / .tar.gz)")

    rec.setdefault("source", {})["resolved_via"] = "user_upload"
    rec["warnings"] = [w for w in (rec.get("warnings") or [])
                       if not w.startswith("source_unavailable")]
    storage.write_paper_json(slug, rec)
    return mappers.map_paper(rec, full=True)


@app.post("/api/topics/{slug}/ideas/run")
def api_idea_run(slug: str, req: IdeaRunRequest):
    """Run the idea agent — ``generate`` ideas from the KB's gaps, or ``expand``
    a user ``sketch`` into one developed idea (enter → generate → store)."""
    _require_meta(slug)
    inputs = {"category_slug": slug, "mode": req.mode,
              "sketch": req.sketch, "count": req.count}
    hint = req.sketch if req.mode == "expand" else f"Generate {req.count} ideas from gaps"
    return MANAGER.start(slug, "idea_agent", "idea", inputs, hint)


# --- static frontend (production / Tauri) ------------------------------------
# In dev the web app is served by Vite; when a production build exists we serve
# it from here so the same backend can back the desktop bundle.
_DIST = Path(__file__).resolve().parents[1] / "frontend" / "dist"
if _DIST.exists():
    app.mount("/", StaticFiles(directory=str(_DIST), html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8787)
