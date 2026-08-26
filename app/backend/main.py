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
from typing import Any  # noqa: E402
import io  # noqa: E402
import json  # noqa: E402
import tarfile  # noqa: E402
import uuid  # noqa: E402
import zipfile  # noqa: E402

from fastapi import FastAPI, File, HTTPException, UploadFile  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import StreamingResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from dev.registry import _truthy  # noqa: E402
from dev.eval_paper_pipeline import _fully_extracted_papers, list_workspaces  # noqa: E402
from src.common.utils import storage  # noqa: E402
from src.agents.ranking_agent.schemas import RankingConfig  # noqa: E402

from app.backend import mappers, sessions  # noqa: E402
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
            out.append(mappers.map_idea(json.loads(f.read_text()), full=False))
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


class IdeaPatchRequest(BaseModel):
    """A researcher's own edit to an idea. Only the parts a human owns."""
    title: str | None = None
    oneLiner: str | None = None
    description: str | None = None
    problem: str | None = None
    # ``{slug: value}`` — markdown string or list of lines. A ``null`` value
    # deletes the section (the same "say it out loud" rule the agent's
    # ``SectionEdit`` uses; an omitted key means leave it alone).
    sections: dict[str, Any] | None = None
    sectionOrder: list[str] | None = None


@app.patch("/api/topics/{slug}/ideas/{idea_id}")
def api_idea_patch(slug: str, idea_id: str, req: IdeaPatchRequest):
    """Apply a hand edit to an idea's spine / open sections.

    The **thinking graph is untouchable**: ``runs[]`` is append-only evidence and
    only the creative thinker writes it, so nothing here can reach it. Same for
    ``relations`` and ``provenance`` — those are written by the orchestrator with a
    rationale attached, never typed over in a text box.
    """
    rec = storage.read_idea_json(slug, idea_id)
    if not rec:
        raise HTTPException(404, f"idea '{idea_id}' not found in '{slug}'")

    for field, key in (("title", "title"), ("oneLiner", "one_liner"),
                       ("description", "description"), ("problem", "problem")):
        value = getattr(req, field)
        if value is not None:
            rec[key] = value

    if req.sections is not None:
        current = dict(rec.get("sections") or {})
        order = [k for k in (rec.get("section_order") or []) if k in current]
        order += [k for k in current if k not in set(order)]
        for key, value in req.sections.items():
            if value is None:
                current.pop(key, None)
                order = [k for k in order if k != key]
            else:
                if key not in current:
                    order.append(key)
                current[key] = value
        rec["sections"] = current
        rec["section_order"] = order

    if req.sectionOrder is not None:
        current = rec.get("sections") or {}
        rec["section_order"] = ([k for k in req.sectionOrder if k in current]
                                + [k for k in current if k not in set(req.sectionOrder)])

    rec["updated_at"] = storage.now_iso()
    storage.write_idea_json(slug, rec)
    return mappers.map_idea(rec)


# --- sessions -----------------------------------------------------------------
# One chat-like surface split into many sessions (vault: *Global Context &
# Session Loading*). A session owns its transcript, its loaded context and the
# ids of everything it touched; the records live in workspace/<slug>/sessions/.

class SessionCreateRequest(BaseModel):
    title: str = ""
    context: list[dict] = []   # optional seed selection, [{kind, id}]


class SessionPatchRequest(BaseModel):
    title: str | None = None


class ContextRequest(BaseModel):
    context: list[dict] = []   # [{kind, id, label?, digest?}]


class SessionSendRequest(BaseModel):
    """One chat turn, run by one of two agents.

    ``agent="creative_thinker"`` is the direct surface: a turn *is* one thinker call.
    The message becomes its ``task``, the newly-dropped items are merged into the
    session's loaded context, and the whole loaded context is rendered as the call's
    ``context``. The mode/width/depth/styles the user picked in the composer are passed
    straight through — the thinker's own knobs, deliberately exposed.

    ``agent="orchestrator"`` plans the turn instead: it reads the message, decides which
    subagents to call and with what, and may call none at all. **The composer knobs are
    ignored on that path** — choosing them is what the planner is for. They stay in this
    model because the thinker path still uses them.

    The web session uses the orchestrator. The direct thinker path remains available for
    development and for callers that explicitly request it.
    """
    text: str = ""
    context: list[dict] = []          # items staged above the composer this turn
    agent: str = "orchestrator"       # orchestrator | creative_thinker
    mode: str = "generation"          # generation | refinement
    ideaId: str | None = None         # refinement: the idea under work
    width: int = 3
    depth: int = 3
    styles: list[str] = []
    toolUse: bool = False
    maxLlmCalls: int = 60


def _require_session(slug: str, session_id: str) -> dict:
    rec = sessions.read_session(slug, session_id)
    if rec is None:
        raise HTTPException(404, f"session '{session_id}' not found in '{slug}'")
    return rec


@app.get("/api/topics/{slug}/sessions")
def api_sessions(slug: str):
    _require_meta(slug)
    return {"sessions": sessions.list_sessions(slug)}


@app.post("/api/topics/{slug}/sessions")
def api_session_create(slug: str, req: SessionCreateRequest):
    _require_meta(slug)
    rec = sessions.create_session(slug, req.title)
    if req.context:
        sessions.add_context(slug, rec, req.context)
        sessions.write_session(slug, rec)
    return rec


@app.get("/api/topics/{slug}/sessions/{session_id}")
def api_session(slug: str, session_id: str):
    return _require_session(slug, session_id)


@app.patch("/api/topics/{slug}/sessions/{session_id}")
def api_session_patch(slug: str, session_id: str, req: SessionPatchRequest):
    rec = _require_session(slug, session_id)
    if req.title is not None:
        rec["title"] = req.title.strip() or rec["title"]
    sessions.write_session(slug, rec)
    return rec


@app.delete("/api/topics/{slug}/sessions/{session_id}")
def api_session_delete(slug: str, session_id: str):
    if not sessions.delete_session(slug, session_id):
        raise HTTPException(404, f"session '{session_id}' not found in '{slug}'")
    return {"ok": True}


@app.post("/api/topics/{slug}/sessions/{session_id}/context")
def api_session_context(slug: str, session_id: str, req: ContextRequest):
    """Load items into the session context *without* sending a turn — the
    click-in-the-panel path. Already-loaded items are a no-op, not a duplicate."""
    rec = _require_session(slug, session_id)
    added = sessions.add_context(slug, rec, req.context)
    sessions.write_session(slug, rec)
    return {"session": rec, "added": added}


@app.delete("/api/topics/{slug}/sessions/{session_id}/context/{kind}/{item_id}")
def api_session_context_remove(slug: str, session_id: str, kind: str, item_id: str):
    rec = _require_session(slug, session_id)
    rec["context"] = [c for c in rec.get("context") or []
                      if not (c.get("kind") == kind and c.get("id") == item_id)]
    sessions.write_session(slug, rec)
    return rec


@app.post("/api/topics/{slug}/sessions/{session_id}/files")
async def api_session_file(slug: str, session_id: str, file: UploadFile = File(...)):
    """A document dropped onto the composer. Stored beside the session and
    registered as a ``file`` context entry (reference, not payload — the digest is
    the head of its text; a pdf keeps only its name until an extractor is wired)."""
    rec = _require_session(slug, session_id)
    data = await file.read()
    if not data:
        raise HTTPException(400, "uploaded file is empty")
    name = Path(file.filename or "document").name
    fdir = sessions.session_files_dir(slug, session_id)
    fdir.mkdir(parents=True, exist_ok=True)
    (fdir / name).write_bytes(data)

    digest = ""
    if not name.lower().endswith(".pdf"):
        digest = data[:4000].decode("utf-8", errors="ignore")
    added = sessions.add_context(slug, rec, [{
        "kind": "file", "id": name, "label": name,
        "digest": digest or f"{name} · {len(data) // 1024} KB (text not extracted yet)",
    }])
    sessions.write_session(slug, rec)
    return {"session": rec, "added": added}


def _send_to_orchestrator(slug: str, session_id: str, rec: dict, text: str,
                          added: list[dict]) -> dict:
    """Start a planned turn, and record the user's message against it.

    The orchestrator takes the *selection*, not the composer's settings: which gaps and
    which ideas are in scope. Those come from the session's loaded context, which is
    already the single record of what the user has put in view — the same list the
    thinker path renders — so nothing new has to be tracked to know what a turn may see.

    The ``run_id`` is minted here rather than left to the graph because the turn's result
    is written to a file named by it, and ``/land`` has to be able to find that file. It
    travels on the run entry's stored inputs, which is where the rest of this module
    looks things up.
    """
    loaded = rec.get("context") or []
    run_id = f"run_{uuid.uuid4().hex[:8]}"
    inputs = {
        "category_slug": slug,
        "session_id": session_id,
        "run_id": run_id,
        "message": text,
        "gap_ids": [c["id"] for c in loaded if c.get("kind") == "gap"],
        "idea_ids": [c["id"] for c in loaded if c.get("kind") == "idea"],
    }
    run = MANAGER.start(slug, "orchestrator_agent", "idea", inputs,
                        text[:80] or f"context +{len(added)}", session_id=session_id)
    message = sessions.append_message(rec, role="user", text=text, context=added,
                                      run_id=run["run_id"], settings={"agent": "orchestrator"})
    if rec.get("title") in ("", "New session") and text:
        rec["title"] = text[:60]
    sessions.write_session(slug, rec)
    return {"session": rec, "message": message, "run": run}


@app.post("/api/topics/{slug}/sessions/{session_id}/send")
def api_session_send(slug: str, session_id: str, req: SessionSendRequest):
    """Send one chat turn — records it, loads its context, starts the thinker.

    Returns ``{session, message, run}``: the updated record, the user message that
    was appended, and the run meta to follow. The **agent** message is appended by
    ``/api/topics/{slug}/sessions/{id}/land`` once the envelope is in, so a client
    that navigates away mid-run still finds the reply on the transcript.
    """
    _require_meta(slug)
    rec = _require_session(slug, session_id)
    text = (req.text or "").strip()
    if not text and not req.context:
        raise HTTPException(400, "a turn needs a message or some context")

    added = sessions.add_context(slug, rec, req.context)

    if req.agent == "orchestrator":
        return _send_to_orchestrator(slug, session_id, rec, text, added)

    mode = "refinement" if str(req.mode).lower().startswith("r") else "generation"
    if mode == "refinement" and not req.ideaId:
        raise HTTPException(400, "refinement needs an idea to work on")

    context_payload = sessions.render_context(slug, rec.get("context") or [], text)
    inputs = {
        "idea": f"ws:{slug}/{req.ideaId}" if req.ideaId else "",
        "mode": mode,
        "width": req.width,
        "depth": req.depth,
        "task": text,
        "styles": ",".join(s for s in req.styles if s),
        "context": json.dumps(context_payload),
        "tool_use": req.toolUse,
        "max_llm_calls": req.maxLlmCalls,
        "triggering_action": f"session {session_id}",
    }
    run = MANAGER.start(slug, "creative_thinker", "idea", inputs,
                        text[:80] or f"context +{len(added)}", session_id=session_id)

    message = sessions.append_message(
        rec, role="user", text=text, context=added, run_id=run["run_id"],
        settings={"mode": mode, "ideaId": req.ideaId, "width": req.width,
                  "depth": req.depth, "styles": req.styles, "toolUse": req.toolUse},
    )
    # The title of an untitled session is its first message — same convention as
    # every chat surface, and it makes the session list readable without work.
    if rec.get("title") in ("", "New session") and text:
        rec["title"] = text[:60]
    sessions.write_session(slug, rec)
    return {"session": rec, "message": message, "run": run}


class SessionLandRequest(BaseModel):
    runId: str


def _save_working_copies(slug: str, session_id: str, run_id: str, envelope: dict) -> list[dict]:
    """Everything the run produced that nobody has accepted yet, as real records.

    Drafts become ``create`` working copies; a refinement that actually changed the
    idea it was handed becomes an ``update`` copy of that record. A run that changed
    nothing produces no update copy — there is nothing to accept.
    """
    saved = []
    for i, draft in enumerate(envelope.get("drafts") or []):
        if sessions.draft_for_run(slug, session_id, run_id, "create", i):
            continue
        saved.append(sessions.save_draft(
            slug, session_id, record=draft, kind="create", run_id=run_id,
            source_index=i, label=draft.get("title", "")))

    idea = envelope.get("idea") or {}
    target = idea.get("idea_id")
    if target and not sessions.draft_for_run(slug, session_id, run_id, "update", None):
        stored = storage.read_idea_json(slug, target)
        # Only when the copy actually differs from the record on disk: an
        # "accept" button over an identical document is a decision about nothing.
        if stored and _differs(stored, idea):
            saved.append(sessions.save_draft(
                slug, session_id, record=idea, kind="update", run_id=run_id,
                target_idea_id=target, label=idea.get("title", "")))
    return saved


def _orchestrator_turn(slug: str, session_id: str, meta: dict) -> dict | None:
    """A planned turn's own record, or None when this run was not one.

    The orchestrator writes what a turn produced — the reply, the ideas it offers, the
    envelopes behind them and which envelope each offer came from — to a file under the
    session as its last act. Read from there rather than off the run entry because a turn
    can involve several thinking runs, and the run entry has room for one.
    """
    if meta.get("agent") != "orchestrator_agent":
        return None
    from src.agents.orchestrator_agent import session as orchestrator_session

    run_id = str((meta.get("inputs") or {}).get("run_id") or "")
    if not run_id:
        return None
    record = orchestrator_session.read_result(slug, session_id, run_id)
    return record if record.get("created_at") else None


def _save_offered_copies(slug: str, session_id: str, run_id: str,
                         turn: dict) -> list[dict]:
    """Every idea a planned turn offered, as a working copy the UI can render.

    One card per offer, whichever thinking run produced it. Two ids are kept because a
    planned turn has two: `run_id` is the run entry the transcript message points at,
    which is how the chat knows which cards belong under which reply, and `offer_run` is
    the turn's own record, which is what the accept step reads the offer back out of.

    Each card also keeps the **handle** it was offered under, so accepting is a single
    call back into the orchestrator's accept step — the one that already knows how to
    find the reasoning behind a card and attach it to what gets written. Two accept
    implementations would have to be kept in step; one cannot drift.
    """
    from src.agents.orchestrator_agent.tools.commit import offer_handle

    offer_run = str(turn.get("run_id") or "")
    saved = []
    for index, record in enumerate(turn.get("ideas") or []):
        handle = offer_handle(offer_run, index, record.get("idea_id"))
        if sessions.draft_for_handle(slug, session_id, handle):
            continue  # a reload, or a second tab: the card is already filed
        saved.append(sessions.save_draft(
            slug, session_id, record=record,
            kind="update" if record.get("idea_id") else "create",
            run_id=run_id, source_index=index, handle=handle, offer_run=offer_run,
            target_idea_id=record.get("idea_id"), label=record.get("title", "")))
    return saved


def _differs(stored: dict, copy: dict) -> bool:
    """Did the run change the parts a human would be accepting?

    ``runs[]`` is excluded on purpose: the thinker appends its trace to every copy,
    so comparing it would make every run look like a content change.
    """
    keys = ("title", "one_liner", "description", "problem", "sections",
            "section_order", "addresses")
    return any(stored.get(k) != copy.get(k) for k in keys)


@app.post("/api/topics/{slug}/sessions/{session_id}/land")
def api_session_land(slug: str, session_id: str, req: SessionLandRequest):
    """Write a finished run's envelope onto the transcript, and file its output.

    Two things land: the agent's reply as a transcript message, and every
    unaccepted product of the run as a **working copy** in the session folder.
    Both are idempotent by ``run_id``, so a reload (or a second tab) re-reads
    rather than appending a duplicate.
    """
    rec = _require_session(slug, session_id)
    meta = MANAGER.get(req.runId)
    if meta is None:
        raise HTTPException(404, f"unknown run '{req.runId}'")
    if meta.get("status") == "running":
        raise HTTPException(409, "run is still going")

    turn = _orchestrator_turn(slug, session_id, meta)
    envelope = meta.get("envelope") or {}
    if turn is not None:
        _save_offered_copies(slug, session_id, req.runId, turn)
    elif envelope:
        _save_working_copies(slug, session_id, req.runId, envelope)

    existing = next((m for m in rec.get("messages") or []
                     if m.get("role") == "agent" and m.get("run_id") == req.runId), None)
    if existing is not None:
        return {"session": rec, "message": existing,
                "drafts": sessions.list_drafts(slug, session_id)}

    if turn is not None:
        # A planned turn always owes a reply and does not always owe ideas — "what can
        # you do?" is answered without calling anything. Having no ideas is therefore an
        # ordinary outcome here, not the failure it is on the direct thinker path.
        message = sessions.append_message(
            rec, role="agent", text=turn.get("answer", ""), run_id=req.runId,
            failed=not turn.get("answer"))
    elif not envelope:
        message = sessions.append_message(
            rec, role="agent", text=meta.get("error") or "The run produced no envelope.",
            run_id=req.runId, failed=True)
    else:
        message = sessions.append_message(
            rec, role="agent", text=envelope.get("answer", ""), run_id=req.runId,
            envelope=envelope)
    sessions.write_session(slug, rec)
    return {"session": rec, "message": message,
            "drafts": sessions.list_drafts(slug, session_id)}


# --- working copies: the accept / dismiss step -------------------------------
# The subagents write nothing. These endpoints are the human commit gate: nothing
# reaches the knowledge base until the user accepts a working copy here.

@app.get("/api/topics/{slug}/sessions/{session_id}/drafts")
def api_session_drafts(slug: str, session_id: str):
    _require_session(slug, session_id)
    return {"drafts": sessions.list_drafts(slug, session_id)}


@app.delete("/api/topics/{slug}/sessions/{session_id}/drafts/{draft_id}")
def api_session_draft_dismiss(slug: str, session_id: str, draft_id: str):
    """Dismiss a working copy — recorded as a decision, not deleted.

    Nothing reaches the knowledge base, but the session keeps the copy and marks it
    ``dismissed``, so the card still reads as decided after a reload.
    """
    _require_session(slug, session_id)
    if sessions.decide_draft(slug, session_id, draft_id, "dismissed") is None:
        raise HTTPException(404, f"no draft '{draft_id}' in session '{session_id}'")
    return {"ok": True, "drafts": sessions.list_drafts(slug, session_id)}


class DraftAcceptRequest(BaseModel):
    """How an ``update`` copy should land.

    ``overwrite`` (the default) writes it over the record the run was handed.
    ``variant`` keeps both: the copy becomes a new idea with a ``variant-of`` edge
    back to that record. This is the choice the thinker's one suggestion asks for —
    a run that carried out its task and moved the idea far enough that overwriting
    would lose it.
    """

    as_: str = Field("overwrite", alias="as")

    model_config = {"populate_by_name": True}


@app.post("/api/topics/{slug}/sessions/{session_id}/drafts/{draft_id}/accept")
def api_session_draft_accept(slug: str, session_id: str, draft_id: str,
                             req: DraftAcceptRequest | None = None):
    """Accept a working copy into the global workspace.

    ``create`` mints an id and writes a new ``idea.json`` — through
    ``idea_agent.apply_envelope``, so the typed relation back to its parent and the
    carved branch of the trace are written the same way they will be when the
    orchestrator does this itself, not re-derived here.

    ``update`` overwrites the record the run was handed. The copy already carries
    the run appended to its ``runs[]``, which is what puts the thinking into the
    file — the whole reason ``runs[]`` is mandatory.

    Either way the working copy is consumed: it is in the knowledge base now, and
    two copies of one record is how they drift apart.
    """
    _require_session(slug, session_id)
    draft = sessions.read_draft(slug, session_id, draft_id)
    if draft is None:
        raise HTTPException(404, f"no draft '{draft_id}' in session '{session_id}'")
    meta = draft.get("_session") or {}
    record = {k: v for k, v in draft.items() if k != "_session"}

    from src.agents.creative_thinker.schemas import Envelope
    from src.agents.idea_agent import apply_envelope, promote_variant

    if meta.get("handle"):
        # Offered by a planned turn: hand it back to the step that offered it. That step
        # replays the envelope the idea actually came from — a turn can hold several —
        # which is what carries the branch of reasoning that produced it onto the record.
        from src.agents.orchestrator_agent.tools.commit import commit_selection

        offer_run = str(meta.get("offer_run") or meta.get("run_id") or "")
        landed = commit_selection(slug, session_id, offer_run,
                                  [str(meta["handle"])])
        if not landed:
            raise HTTPException(409, "the run that offered this draft no longer has it")
        sessions.decide_draft(slug, session_id, draft_id, "accepted",
                              result_idea_id=landed[0])
        rec = _require_session(slug, session_id)
        sessions.touch(rec, landed)
        sessions.write_session(slug, rec)
        return {"result": {"created": landed, "written": []}, "session": rec,
                "drafts": sessions.list_drafts(slug, session_id)}

    if meta.get("kind") == "update":
        target = meta.get("target_idea_id")
        parent = storage.read_idea_json(slug, target) if target else None
        if not parent:
            raise HTTPException(409, f"the idea '{target}' this draft updates is gone")
        if (req.as_ if req else "overwrite") == "variant":
            # Both records survive: the run's rewrite lands as its own idea, edged
            # back to the one it grew out of. The parent is left untouched — the
            # run's thinking is already in the copy's ``runs[]``.
            rationale = _divergence_rationale(meta.get("run_id"))
            result = promote_variant(slug, record, parent_idea_id=target,
                                     rationale=rationale,
                                     run_id=(record.get("runs") or [{}])[-1].get("run_id", ""))
        else:
            envelope = Envelope.model_validate({"idea": record})
            result = apply_envelope(slug, envelope, persist_idea=True)
    else:
        run_meta = MANAGER.get(str(meta.get("run_id") or ""))
        envelope_raw = (run_meta or {}).get("envelope")
        index = meta.get("source_index")
        # The run's envelope carries the parent and the suggested operation, which
        # is what `apply_envelope` needs to write the edge and carve the branch.
        # Without it (a pruned run index) the draft is still writable on its own —
        # it just lands without a relation, which is better than not landing.
        if envelope_raw and isinstance(index, int):
            envelope = Envelope.model_validate(envelope_raw)
            result = apply_envelope(slug, envelope, accept_drafts=[index],
                                    persist_idea=False)
        else:
            envelope = Envelope.model_validate({"idea": {}, "drafts": [record]})
            result = apply_envelope(slug, envelope, accept_drafts=[0],
                                    persist_idea=False)

    landed = list(result.get("created") or []) + list(result.get("written") or [])
    sessions.decide_draft(slug, session_id, draft_id, "accepted",
                          result_idea_id=landed[0] if landed else "")
    rec = _require_session(slug, session_id)
    sessions.touch(rec, list(result.get("created") or []) + list(result.get("written") or []))
    sessions.write_session(slug, rec)
    return {"result": result, "session": rec,
            "drafts": sessions.list_drafts(slug, session_id)}


def _divergence_rationale(run_id: str | None) -> str:
    """The suggestion's own words, so the edge carries why the two ideas split.

    Re-deriving it here would say something the user never saw; the run said it
    already, in the ``create-variant`` it raised.
    """
    envelope = (MANAGER.get(str(run_id or "")) or {}).get("envelope") or {}
    for op in envelope.get("suggested_operations") or []:
        if op.get("type") == "create-variant":
            return op.get("diagnosis") or op.get("rationale") or ""
    return "kept as a variant rather than overwriting the idea it came from"


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
    """Ideate from the KB — ``generate`` candidates from the master gaps, or
    ``expand`` a user ``sketch`` into developed ones.

    This is the *quick* path off the Ideas view; the session chat is the full one.
    Both land on the same worker: the idea agent's orchestrator does not exist yet,
    so the creative thinker is called directly, with the workspace's ranked gaps as
    its context and the sketch (if any) as its task.
    """
    _require_meta(slug)
    gaps = [{"gap_id": g["id"], "title": g["title"], "description": g["description"]}
            for g in _read_gaps(slug, "master")[:6]]
    context = {"gaps": gaps}
    if req.sketch.strip():
        context["user_input"] = req.sketch.strip()
    inputs = {
        "idea": "", "mode": "generation", "width": max(1, int(req.count)), "depth": 1,
        "task": req.sketch.strip() or "Propose ideas that attack these gaps.",
        "styles": "", "context": json.dumps(context), "tool_use": False,
        "max_llm_calls": 60, "triggering_action": "ideas view",
    }
    hint = req.sketch if req.mode == "expand" else f"Generate {req.count} ideas from gaps"
    return MANAGER.start(slug, "creative_thinker", "idea", inputs, hint)


# --- static frontend (production / Tauri) ------------------------------------
# In dev the web app is served by Vite; when a production build exists we serve
# it from here so the same backend can back the desktop bundle.
_DIST = Path(__file__).resolve().parents[1] / "frontend" / "dist"
if _DIST.exists():
    app.mount("/", StaticFiles(directory=str(_DIST), html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8787)
