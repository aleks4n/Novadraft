"""PaperSummaryAgent entry point (Stage 3).

Reads `content.sections` from paper.json, calls the LLM once with a structured
JSON-output prompt, and writes the result to `paper.json::summary`.
"""
from __future__ import annotations

import json

from paper_fetch import storage


def _read_category_meta(category_slug: str) -> dict | None:
    path = storage.category_dir(category_slug) / "meta.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None

from _llm import chat_json
from .prompt import SYSTEM_PROMPT, build_user_prompt


class PaperNotFoundError(Exception):
    pass


class NotExtractedError(Exception):
    pass


def _write(category_slug: str, record: dict) -> None:
    pdir = storage.paper_dir(category_slug, record["paper_id"])
    (pdir / "paper.json").write_text(json.dumps(record, indent=2, ensure_ascii=False))


def summarize(category_slug: str, paper_id: str, force: bool = False) -> dict:
    record = storage.read_paper_json(category_slug, paper_id)
    if record is None:
        raise PaperNotFoundError(f"{category_slug}/{paper_id}")

    processing = record.setdefault("processing", {})
    if not processing.get("extracted"):
        raise NotExtractedError(
            f"{paper_id}: run paper_extract first (processing.extracted is false)"
        )
    if processing.get("summarized") and not force:
        return {"status": "skipped", "paper_id": paper_id, "reason": "already_summarized"}

    sections = ((record.get("content") or {}).get("sections")) or {}
    if not sections:
        record.setdefault("warnings", []).append("summary_skipped: no sections")
        _write(category_slug, record)
        return {"status": "skipped", "paper_id": paper_id, "reason": "no_sections"}

    user_prompt = build_user_prompt(
        title=record.get("title") or "(unknown)",
        authors=record.get("authors") or [],
        year=record.get("year"),
        abstract=record.get("abstract"),
        sections=sections,
        category=_read_category_meta(category_slug),
    )

    summary = chat_json(SYSTEM_PROMPT, user_prompt)

    record["summary"] = summary
    processing["summarized"] = True
    _write(category_slug, record)

    return {
        "status": "ok",
        "paper_id": paper_id,
        "paper_type": summary.get("paper_type"),
        "n_contributions": len(summary.get("contributions") or []),
        "n_open_threads": len(summary.get("open_threads") or []),
    }
