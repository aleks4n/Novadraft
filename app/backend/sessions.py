"""Ideation **sessions** — the persistent record behind the session chat surface.

The vault's *Global Context & Session Loading* note is the design of record: one
chat-like surface split into many sessions, each storing "its own transcript, its
loaded-context history and the ids of every artefact it touched, so a decision can
be traced back to the conversation that produced it."

Storage mirrors the rest of the workspace — plain JSON per record, the files are
the truth. One **isolated folder per session**, as the creative thinker's SPEC
names it::

    workspace/<slug>/sessions/<session_id>/session.json   transcript + context
    workspace/<slug>/sessions/<session_id>/drafts/*.json  working copies
    workspace/<slug>/sessions/<session_id>/files/<name>   dropped pdfs / notes

**Working copies** are the point of the folder. The creative thinker writes
nothing: it returns a modified copy of the idea and a list of drafts, and until a
human accepts one those records have no business in the global workspace — but
they cannot live only in a chat bubble either, or the session's own work
disappears on reload. So each lands here as a standalone ``idea.json``: real
records, in the real schema, in a folder that is not the knowledge base.

Three ways one leaves: **accepted** (written into the global workspace, minting an
id for a new idea or overwriting the record it modified), **dismissed** (the file
is deleted — a rejected draft is not a smaller idea, it is not an idea), or left
alone, in which case it is still there next session.

Two representations of one selection, both served from here:

- ``context`` — the session's **loaded** context, append-only across turns. This is
  the single source of truth for what the model can see; the frontend highlights
  these ids in the graph panel and lists them in the Loaded Context window.
- ``messages[].context`` — what *that turn* added, so the transcript shows when
  each item entered.

Context entries are **references, not payload** (id + short digest); the full text
is re-read from the workspace when a run needs it (:func:`render_context`).
"""
from __future__ import annotations

import json
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.common.utils import storage

# Kinds a context reference can have. ``note`` is free text the user pasted;
# ``file`` is an uploaded document (pdf/text) stored beside the session.
KINDS = ("gap", "idea", "paper", "file", "note")

_MAX_DIGEST = 400


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def new_session_id() -> str:
    return f"sess_{uuid.uuid4().hex[:8]}"


def new_message_id() -> str:
    return f"msg_{uuid.uuid4().hex[:8]}"


# --- paths -------------------------------------------------------------------

def sessions_dir(slug: str) -> Path:
    return storage.category_dir(slug) / "sessions"


def session_dir(slug: str, session_id: str) -> Path:
    return sessions_dir(slug) / session_id


def session_path(slug: str, session_id: str) -> Path:
    return session_dir(slug, session_id) / "session.json"


def session_files_dir(slug: str, session_id: str) -> Path:
    return session_dir(slug, session_id) / "files"


def session_drafts_dir(slug: str, session_id: str) -> Path:
    return session_dir(slug, session_id) / "drafts"


# --- read / write ------------------------------------------------------------

def read_session(slug: str, session_id: str) -> dict | None:
    path = session_path(slug, session_id)
    if not path.exists():
        _migrate_flat(slug, session_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def write_session(slug: str, record: dict) -> Path:
    path = session_path(slug, record["session_id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    record["updated_at"] = _now()
    path.write_text(json.dumps(record, indent=1, default=str))
    return path


def _migrate_flat(slug: str, session_id: str) -> None:
    """Lift a pre-folder ``sessions/<id>.json`` into ``sessions/<id>/session.json``.

    The flat layout predates working copies, which need a folder to live in. Kept
    because a session is a *record of decisions* — dropping one to change where it
    is filed would be the worst possible reason to lose it.
    """
    flat = sessions_dir(slug) / f"{session_id}.json"
    if not flat.exists():
        return
    target = session_path(slug, session_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    flat.rename(target)
    old_files = sessions_dir(slug) / "files" / session_id
    if old_files.is_dir():
        old_files.rename(session_files_dir(slug, session_id))


def list_sessions(slug: str, *, full: bool = False) -> list[dict]:
    """Every session of a workspace, newest first. ``full=False`` drops the
    transcript — the sidebar only needs the header line."""
    sdir = sessions_dir(slug)
    if not sdir.exists():
        return []
    for flat in list(sdir.glob("*.json")):
        _migrate_flat(slug, flat.stem)
    out = []
    for d in sdir.iterdir():
        if not d.is_dir():
            continue
        try:
            rec = json.loads((d / "session.json").read_text())
        except (json.JSONDecodeError, OSError):
            continue
        out.append(rec if full else summarize(rec))
    out.sort(key=lambda r: r.get("updated_at") or "", reverse=True)
    return out


def summarize(rec: dict) -> dict:
    """The list-row form: identity, counts, and the last thing said."""
    messages = rec.get("messages") or []
    last = next((m for m in reversed(messages) if m.get("text")), None)
    return {
        "session_id": rec.get("session_id", ""),
        "title": rec.get("title") or "Untitled session",
        "createdAt": rec.get("created_at", ""),
        "updatedAt": rec.get("updated_at", ""),
        "messageCount": len(messages),
        "contextCount": len(rec.get("context") or []),
        "touched": rec.get("touched") or [],
        "preview": (last or {}).get("text", "")[:140],
    }


def create_session(slug: str, title: str = "") -> dict:
    rec = {
        "session_id": new_session_id(),
        "category_slug": slug,
        "title": title.strip() or "New session",
        "created_at": _now(),
        "updated_at": _now(),
        "messages": [],
        "context": [],
        "touched": [],
    }
    write_session(slug, rec)
    return rec


def delete_session(slug: str, session_id: str) -> bool:
    """Drop a session and everything it owned — transcript, files, working copies.

    Working copies go with it *because* they were never in the knowledge base:
    anything the session produced that mattered was accepted into the workspace,
    and what was not is exactly what the user chose not to keep.
    """
    d = session_dir(slug, session_id)
    if not d.is_dir():
        return False
    shutil.rmtree(d)
    return True


# --- context -----------------------------------------------------------------

def _digest(text: str) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return text[:_MAX_DIGEST]


def resolve_ref(slug: str, ref: dict) -> dict | None:
    """A raw ``{kind, id}`` from the frontend → a stored context entry.

    Resolved against the workspace so the label/digest on the record are the
    workspace's, not whatever the client happened to send — an entry has to stay
    meaningful when the session is reopened months later.
    """
    kind = str(ref.get("kind") or "").strip()
    ident = str(ref.get("id") or "").strip()
    if kind not in KINDS:
        return None

    label, digest = str(ref.get("label") or ""), _digest(ref.get("digest") or "")
    if kind == "gap":
        _stage, rec = storage.find_gap(slug, ident)
        if rec is None:
            return None
        label = rec.get("title") or rec.get("canonical_description") or ident
        digest = _digest(rec.get("description") or rec.get("canonical_description") or "")
    elif kind == "idea":
        rec = storage.read_idea_json(slug, ident)
        if rec is None:
            return None
        label = rec.get("title") or ident
        digest = _digest(rec.get("one_liner") or rec.get("description") or "")
    elif kind == "paper":
        rec = storage.read_paper_json(slug, ident)
        if rec is None:
            return None
        meta = rec.get("metadata") or {}
        label = meta.get("title") or rec.get("title") or ident
        digest = _digest((rec.get("summary") or {}).get("context_summary")
                         or meta.get("abstract") or "")
    elif kind == "note":
        ident = ident or f"note_{uuid.uuid4().hex[:6]}"
        label = label or "Pasted note"
    elif kind == "file":
        if not ident:
            return None
        label = label or ident

    return {"kind": kind, "id": ident, "label": label or ident,
            "digest": digest, "at": _now()}


def add_context(slug: str, rec: dict, refs: list[dict]) -> list[dict]:
    """Merge references into the session's loaded context; returns what was new.

    Adding an item already loaded is a no-op rather than a duplicate — the list is
    a set of what the model can see, not a log of clicks.
    """
    have = {(c["kind"], c["id"]) for c in rec.get("context") or []}
    added = []
    for ref in refs or []:
        entry = resolve_ref(slug, ref)
        if entry is None or (entry["kind"], entry["id"]) in have:
            continue
        have.add((entry["kind"], entry["id"]))
        added.append(entry)
    rec.setdefault("context", []).extend(added)
    return added


def render_context(slug: str, entries: list[dict], user_input: str = "") -> dict[str, Any]:
    """Loaded context → the creative thinker's ``context`` dict.

    Reference-not-payload: the stored entry keeps a digest, but the full record is
    re-read here so a run always sees the current state of the workspace rather
    than a snapshot from whenever the item was dropped in.
    """
    out: dict[str, Any] = {"gaps": [], "papers": [], "ideas": [], "documents": []}
    for entry in entries or []:
        kind, ident = entry.get("kind"), entry.get("id", "")
        if kind == "gap":
            _stage, rec = storage.find_gap(slug, ident)
            out["gaps"].append({
                "gap_id": ident,
                "title": (rec or {}).get("title") or entry.get("label", ""),
                "description": (rec or {}).get("description") or entry.get("digest", ""),
            })
        elif kind == "idea":
            rec = storage.read_idea_json(slug, ident) or {}
            out["ideas"].append({
                "idea_id": ident,
                "title": rec.get("title") or entry.get("label", ""),
                "description": rec.get("description") or entry.get("digest", ""),
            })
        elif kind == "paper":
            rec = storage.read_paper_json(slug, ident) or {}
            meta = rec.get("metadata") or {}
            out["papers"].append({
                "paper_id": ident,
                "title": meta.get("title") or entry.get("label", ""),
                "summary": (rec.get("summary") or {}).get("context_summary")
                           or entry.get("digest", ""),
            })
        elif kind in ("file", "note"):
            out["documents"].append({
                "id": ident,
                "title": entry.get("label", ""),
                "text": entry.get("digest", ""),
            })
    if user_input.strip():
        out["user_input"] = user_input.strip()
    return {k: v for k, v in out.items() if v}


# --- transcript --------------------------------------------------------------

def append_message(rec: dict, *, role: str, text: str, **extra: Any) -> dict:
    msg = {"id": new_message_id(), "role": role, "text": text, "at": _now(), **extra}
    rec.setdefault("messages", []).append(msg)
    return msg


def touch(rec: dict, ids: list[str]) -> None:
    """Record artefacts this session created or changed (the traceability half)."""
    seen = set(rec.setdefault("touched", []))
    for ident in ids:
        if ident and ident not in seen:
            seen.add(ident)
            rec["touched"].append(ident)


# --- working copies ----------------------------------------------------------
# What a run produced but nobody has accepted: the drafts it carved out, and the
# modified copy of the idea it was refining. Real `idea.json` records in the
# session's own folder — not the knowledge base, and not a chat bubble either.

# How a working copy relates to the global workspace:
#   "create"  a new idea; accepting mints an id and writes it
#   "update"  a modified copy of an existing record; accepting overwrites it
DraftKind = str


def new_draft_id() -> str:
    return f"draft_{uuid.uuid4().hex[:8]}"


def _draft_path(slug: str, session_id: str, draft_id: str) -> Path:
    return session_drafts_dir(slug, session_id) / f"{draft_id}.json"


def save_draft(slug: str, session_id: str, *, record: dict, kind: DraftKind,
               run_id: str, source_index: int | None = None,
               target_idea_id: str | None = None, label: str = "",
               handle: str = "") -> dict:
    """Write one working copy into the session folder.

    The stored file is the `idea.json` **plus** a small `_session` block naming
    where it came from: the run, its index in that run's envelope, and what
    accepting it would do. Keeping the provenance beside the record is what lets
    an accept happen days later, from a reloaded page, without the envelope still
    being in anyone's memory.

    `handle` is set for a copy a *planned* turn offered. Such a turn may have thought
    several times, so "index into the envelope" is not enough to say which record this
    is — the handle names it across the whole turn, and accepting hands it straight back
    to the orchestrator rather than re-deriving the write here.
    """
    draft_id = new_draft_id()
    payload = dict(record)
    payload["_session"] = {
        "draft_id": draft_id,
        "session_id": session_id,
        "kind": kind,                      # "create" | "update"
        "run_id": run_id,
        "source_index": source_index,      # index into envelope.drafts, for "create"
        "handle": handle,                  # what a planned turn offered it as
        "target_idea_id": target_idea_id,  # the record an "update" would overwrite
        "label": label or record.get("title", ""),
        "status": "open",                  # open | accepted | dismissed
        "created_at": _now(),
    }
    path = _draft_path(slug, session_id, draft_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=1, default=str))
    return payload


def list_drafts(slug: str, session_id: str) -> list[dict]:
    """Every working copy of a session, oldest first — **decided ones included**.

    A decision is part of the transcript. Accepting or dismissing used to delete the
    file, so a reloaded page showed the card exactly as it had looked before the
    choice was made, offering it again. The file stays and carries its ``status``;
    the UI renders "saved as …" or "dismissed" instead of the buttons.
    """
    ddir = session_drafts_dir(slug, session_id)
    if not ddir.is_dir():
        return []
    out = []
    for f in sorted(ddir.glob("*.json")):
        try:
            out.append(json.loads(f.read_text()))
        except (json.JSONDecodeError, OSError):
            continue
    out.sort(key=_draft_order)
    return out


def _draft_order(record: dict) -> tuple:
    """Oldest first, then the order the run offered them in.

    ``created_at`` alone is not enough: it has second resolution and a run files all of
    its copies at once, so every card of one run ties. The tie then fell to the order the
    filesystem happened to list random draft ids in — the same run showing its candidates
    in a different order on a reload. ``source_index`` is the run's own numbering, which
    is the order the user should see them in. The update copy of a refined record has
    none, and belongs after the drafts of the same run rather than before them.
    """
    meta = record.get("_session") or {}
    index = meta.get("source_index")
    return (meta.get("created_at") or "", meta.get("run_id") or "",
            index is None, index or 0)


def read_draft(slug: str, session_id: str, draft_id: str) -> dict | None:
    path = _draft_path(slug, session_id, draft_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def decide_draft(slug: str, session_id: str, draft_id: str, status: str, *,
                 result_idea_id: str = "") -> dict | None:
    """Record the decision on the working copy — ``accepted`` or ``dismissed``.

    The record itself is left exactly as it was: for an accepted copy it is what was
    written (and ``result_idea_id`` says where), for a dismissed one it is what was
    turned down. Both are answers to "what did I decide about this run?", which is a
    question the session is the only place to ask.
    """
    draft = read_draft(slug, session_id, draft_id)
    if draft is None:
        return None
    meta = draft.setdefault("_session", {})
    meta["status"] = status
    meta["decided_at"] = _now()
    if result_idea_id:
        meta["result_idea_id"] = result_idea_id
    _draft_path(slug, session_id, draft_id).write_text(json.dumps(draft, indent=1, default=str))
    return draft


def draft_for_run(slug: str, session_id: str, run_id: str, kind: str,
                  source_index: int | None) -> dict | None:
    """The working copy already saved for one envelope item, if any.

    Landing a run is idempotent, and so is this: a reload must not produce a
    second copy of the same draft. Decided copies count — re-offering something the
    user already accepted or dismissed is the bug this prevents.
    """
    for d in list_drafts(slug, session_id):
        meta = d.get("_session") or {}
        if (meta.get("run_id") == run_id and meta.get("kind") == kind
                and meta.get("source_index") == source_index):
            return d
    return None


def draft_for_handle(slug: str, session_id: str, handle: str) -> dict | None:
    """The working copy already saved for one offer of a planned turn, if any.

    Same idempotence as :func:`draft_for_run`, keyed on the handle instead: a planned
    turn may have thought several times, so the index alone does not identify a record.
    """
    if not handle:
        return None
    for d in list_drafts(slug, session_id):
        if (d.get("_session") or {}).get("handle") == handle:
            return d
    return None
