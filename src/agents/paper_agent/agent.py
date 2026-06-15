"""PaperAgent — runs a single paper through the full pipeline.

Wires the four stages in sequence:
  1. fetch      (common tool, shared with other agents)
  2. extract    (common tool, shared with other agents)
  3. summarize  (subagent)
  4. gap_agent  (subagent)

The fetch tool itself does not write to fetch_log.jsonl — this agent is the
single place that logs fetch outcomes for the category workspace.
"""
from __future__ import annotations

from typing import Callable

from ...common.tools.paper_extract import extract
from ...common.tools.paper_fetch import PaperAlreadyExistsError, fetch
from ...common.utils import storage
from .subagents.gap_agent import extract_gaps
from .subagents.paper_summary import summarize

# Ordered pipeline steps, used to report progress via ``on_step``.
STEPS = ["fetch", "extract", "summary", "gaps"]


def run(category_slug: str, query: str, force: bool = False,
        on_step: Callable[[dict], None] | None = None) -> dict:
    """Fetch a paper into ``category_slug`` and run it through extract,
    summarize, and gap extraction.

    Returns the fetch result as-is if the fetch was ambiguous (no paper was
    stored). On a duplicate, logs the duplicate and continues the pipeline on
    the existing paper (extract/summarize/gap are idempotent).

    If given, ``on_step`` is called with ``{"step": <name>, "status": ...}``
    before and after each pipeline step (fetch/extract/summary/gaps), so
    callers (e.g. the dev UI) can show live progress.
    """
    def _notify(step: str, status: str, **extra) -> None:
        if on_step is not None:
            on_step({"step": step, "status": status, **extra})

    _notify("fetch", "running")
    try:
        fetch_result = fetch(category_slug, query)
    except PaperAlreadyExistsError as e:
        storage.log_fetch(category_slug, **e.log_entry)
        paper_id = e.paper_id
        fetch_result = {"status": "duplicate", "paper_id": paper_id}
    except Exception as e:
        log_entry = getattr(e, "log_entry", None)
        if log_entry is not None:
            storage.log_fetch(category_slug, **log_entry)
        _notify("fetch", "error", error=str(e))
        raise
    else:
        storage.log_fetch(category_slug, **fetch_result["log_entry"])
        if fetch_result["status"] == "ambiguous":
            _notify("fetch", "done", result=fetch_result)
            for step in STEPS[1:]:
                _notify(step, "skipped")
            return fetch_result
        paper_id = fetch_result["paper"]["paper_id"]
    _notify("fetch", "done", result=fetch_result)

    _notify("extract", "running")
    extract_result = extract(category_slug, paper_id, force=force)
    _notify("extract", "done", result=extract_result)

    _notify("summary", "running")
    summary_result = summarize(category_slug, paper_id, force=force)
    _notify("summary", "done", result=summary_result)

    _notify("gaps", "running")
    gaps_result = extract_gaps(category_slug, paper_id, force=force)
    _notify("gaps", "done", result=gaps_result)

    return {
        "status": "ok",
        "paper_id": paper_id,
        "fetch": fetch_result,
        "extract": extract_result,
        "summary": summary_result,
        "gaps": gaps_result,
    }
