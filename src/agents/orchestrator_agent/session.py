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
    answer: str = "", ideas: list[Idea] | None = None,
) -> Path:
    """Persist one run's outcome."""
    d = session_dir(category_slug, session_id)
    d.mkdir(parents=True, exist_ok=True)
    out = _path(category_slug, session_id, run_id)
    out.write_text(
        json.dumps(
            {
                "run_id": run_id,
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
        return {"run_id": run_id, "answer": "", "ideas": []}
    return json.loads(p.read_text(encoding="utf-8"))


def read_ideas(category_slug: str, session_id: str, run_id: str) -> list[Idea]:
    """The drafts `run_id` offered — the only ideas commit is allowed to write."""
    return _IDEAS.validate_python(read_result(category_slug, session_id, run_id)["ideas"])
