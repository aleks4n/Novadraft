"""Per-session state: run results, and the investigator's fetches.

    workspace/<category>/sessions/<session_id>/
        <run_id>.json   one run's outcome: the answer plus the drafts it offered
        papers/         papers the investigator fetched for this session

A thinking run ends by writing its drafts here and returning them to the chat. When the
user accepts some, `commit_selection` reads them back by `run_id` — so what gets written
to the idea graph is exactly what the user was shown, not a regenerated set.

This is what lets a run finish instead of suspending mid-graph: the pending state lives on
disk between turns rather than in a LangGraph checkpointer. See SPEC.md.

It also doubles as the run's result record, because `GraphRunLog.run()` returns only the
output it was handed and the streaming path hands it none — the same reason the validator
re-reads its finalized gap instead of using the return value.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import TypeAdapter

from src.common.schemas.idea import Idea
from src.common.utils import storage

_IDEAS = TypeAdapter(list[Idea])


def session_dir(category_slug: str, session_id: str) -> Path:
    return storage.category_dir(category_slug) / "sessions" / session_id


def session_papers_dir(category_slug: str, session_id: str) -> Path:
    """Where the investigator puts what it fetches — session-scoped until promoted."""
    return session_dir(category_slug, session_id) / "papers"


def _path(category_slug: str, session_id: str, run_id: str) -> Path:
    return session_dir(category_slug, session_id) / f"{run_id}.json"


def write_result(
    category_slug: str, session_id: str, run_id: str, *,
    message: str = "", answer: str = "", ideas: list[Idea] | None = None,
) -> Path:
    """Persist one run's outcome.

    `message` is the user's turn. Without it a run records only half the exchange, and a
    later turn reading the session back would see the replies with nothing they answered.

    `created_at` orders the runs. Run ids are random, so they sort into no sequence, and
    file mtimes are lost the moment a workspace is copied.
    """
    d = session_dir(category_slug, session_id)
    d.mkdir(parents=True, exist_ok=True)
    out = _path(category_slug, session_id, run_id)
    out.write_text(
        json.dumps(
            {
                "run_id": run_id,
                "created_at": storage.now_iso(),
                "message": message,
                "answer": answer,
                "ideas": [i.model_dump(mode="json") for i in (ideas or [])],
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return out


def read_result(category_slug: str, session_id: str, run_id: str) -> dict:
    """One run's outcome; empty defaults if it wrote none."""
    p = _path(category_slug, session_id, run_id)
    if not p.exists():
        return {"run_id": run_id, "created_at": "", "message": "", "answer": "", "ideas": []}
    return json.loads(p.read_text(encoding="utf-8"))


def read_runs(category_slug: str, session_id: str, limit: int = 3,
              before: str = "") -> list[dict]:
    """The session's most recent runs, oldest first.

    `before` excludes a run id — the turn in progress has not written its own file yet,
    but a retry or a re-run of the same id would, and it should not read itself back.

    Runs that predate `created_at` sort first, which puts them where they belong: older
    than anything written since.
    """
    d = session_dir(category_slug, session_id)
    if not d.exists():
        return []
    runs = []
    for path in d.glob("run_*.json"):
        if path.stem == before:
            continue
        try:
            runs.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    runs.sort(key=lambda r: r.get("created_at") or "")
    return runs[-limit:] if limit else runs


def read_ideas(category_slug: str, session_id: str, run_id: str) -> list[Idea]:
    """The drafts `run_id` offered — the only ideas commit is allowed to write."""
    return _IDEAS.validate_python(read_result(category_slug, session_id, run_id)["ideas"])
