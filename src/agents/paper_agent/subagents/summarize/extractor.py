"""summarize subagent — extractor (Stage 3): one stateless LLM pass.

Reads `content.sections` from paper.json, calls the LLM once with a structured
JSON-output prompt, and writes the result to `paper.json::summary`. Called
repeatedly by `summarize.summarize()`'s loop on a regenerate round, with
`feedback` from the critic *and* the previous summary itself appended to the
prompt.

On a feedback round the LLM is constrained to `PaperSummaryPatch` — only the
top-level fields named by the critique's `field` paths — and the result is
merged into the existing summary in code (`dict.update`), not written
wholesale. A field no issue mentioned is therefore never touched, regardless
of what the model does or doesn't echo back.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from .....common.utils import storage
from .....common.utils.runlog import log_event, step

_NOVADRAFT_ROOT = Path(__file__).resolve().parents[5]
if str(_NOVADRAFT_ROOT) not in sys.path:
    sys.path.insert(0, str(_NOVADRAFT_ROOT))

from src.llm.registry import chat_json  # noqa: E402
from .....common.schemas.summary import PaperSummary, PaperSummaryPatch
from .extractor_prompt import SYSTEM_PROMPT, build_user_prompt

_PATCH_FIELD_NAMES = set(PaperSummaryPatch.model_fields.keys())


def _patch_fields_from_feedback(feedback: list[dict]) -> list[str]:
    """Top-level PaperSummary field names implicated by a feedback round,
    derived from each issue's dotted `field` path (e.g. "approach.assumptions"
    -> "approach"). A grounding-driven issue may name several plausible
    fields at once joined by "/" (it's a deterministic value-in-text check
    that doesn't know which field a value lives in, e.g.
    "metrics/datasets/results.headline") — each alternative is resolved to
    its own top-level field. Unknown/malformed field paths are dropped
    rather than widening the patch to fields no issue actually named."""
    fields: list[str] = []
    for issue in feedback:
        raw = (issue.get("field") or "").strip()
        for alt in raw.split("/"):
            top = alt.split(".")[0].strip()
            if top in _PATCH_FIELD_NAMES and top not in fields:
                fields.append(top)
    return fields


class PaperNotFoundError(Exception):
    pass


class NotExtractedError(Exception):
    pass


def _read_category_meta(category_slug: str) -> dict | None:
    path = storage.category_dir(category_slug) / "meta.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _write(category_slug: str, record: dict) -> None:
    pdir = storage.paper_dir(category_slug, record["paper_id"])
    (pdir / "paper.json").write_text(json.dumps(record, indent=2, ensure_ascii=False))


def run_extract(category_slug: str, paper_id: str, force: bool = False,
                feedback: list[dict] | None = None,
                missing_hints: list[str] | None = None) -> dict:
    """One extractor pass.

    `feedback` (optional) is a list of critic issues (see `summarize.critic`)
    — when given (and `missing_hints` is not), the LLM returns a
    `PaperSummaryPatch` covering only the top-level fields those issues name,
    which is merged into the existing summary rather than replacing it
    wholesale.

    `missing_hints` (optional): the critic's coverage verdict — something the
    source states that no field reflects at all. Because that content isn't
    tied to a known field path, a round with `missing_hints` always gets the
    FULL `PaperSummary` schema back (never a narrow patch), even if `feedback`
    is also present — the model needs the freedom to add the missing item
    wherever it actually belongs.

    Passing `feedback` or `missing_hints` implies a re-summarize and bypasses
    the `already_summarized` skip (callers should also pass `force=True`).
    """
    with step("extractor", paper_id=paper_id, feedback=bool(feedback),
             missing_hints=bool(missing_hints)) as node:
        def _done(result: dict) -> dict:
            node["data"]["result"] = result
            return result

        record = storage.read_paper_json(category_slug, paper_id)
        if record is None:
            raise PaperNotFoundError(f"{category_slug}/{paper_id}")

        processing = record.setdefault("processing", {})
        if not processing.get("extracted"):
            raise NotExtractedError(
                f"{paper_id}: run paper_extract first (processing.extracted is false)"
            )
        if processing.get("summarized") and not force and not feedback and not missing_hints:
            log_event("already summarized, skipping", paper_id=paper_id)
            return _done({"status": "skipped", "paper_id": paper_id, "reason": "already_summarized"})

        sections = ((record.get("content") or {}).get("sections")) or {}
        if not sections:
            log_event("no sections to summarize, skipping", paper_id=paper_id)
            record.setdefault("warnings", []).append("summary_skipped: no sections")
            _write(category_slug, record)
            return _done({"status": "skipped", "paper_id": paper_id, "reason": "no_sections"})

        use_patch = bool(feedback) and not missing_hints
        patch_fields = _patch_fields_from_feedback(feedback) if use_patch else None
        has_context = bool(feedback) or bool(missing_hints)
        metadata = record.get("metadata") or {}
        user_prompt = build_user_prompt(
            title=metadata.get("title") or "(unknown)",
            authors=metadata.get("authors") or [],
            year=metadata.get("year"),
            abstract=metadata.get("abstract"),
            sections=sections,
            category=_read_category_meta(category_slug),
            feedback=feedback,
            previous_summary=record.get("summary") if has_context else None,
            patch_fields=patch_fields,
            missing_hints=missing_hints,
        )

        schema = PaperSummaryPatch if use_patch else PaperSummary
        raw = chat_json(SYSTEM_PROMPT, user_prompt, response_schema=schema)

        if use_patch:
            # Patch round: `raw` has every PaperSummaryPatch field, most of
            # them None (unrequested). Treat "every requested field came back
            # None" as a failed call, same as the old empty-dict check — a
            # patch with no actual content is no better than no response.
            changed = {k: v for k, v in raw.items() if k in (patch_fields or []) and v is not None}
            empty_response = not changed
        else:
            changed = raw
            empty_response = not raw

        if empty_response:
            # Don't let a flaky/empty LLM response clobber a previously good
            # summary still sitting in `record` (this matters most on a
            # feedback-driven regenerate round, which calls us against an
            # already-summarized paper).
            log_event("empty LLM response, keeping previous summary", paper_id=paper_id)
            record.setdefault("warnings", []).append("summary_failed: empty LLM response")
            _write(category_slug, record)
            return _done({"status": "error", "paper_id": paper_id, "reason": "empty_llm_response"})

        if use_patch:
            # Field-scoped merge: only the fields the critique actually named
            # are touched, so a field no issue mentioned cannot drift between
            # rounds — see PaperSummaryPatch's docstring.
            summary = dict(record.get("summary") or {})
            summary.update(changed)
        else:
            summary = changed

        record["summary"] = summary
        processing["summarized"] = True
        _write(category_slug, record)

        return _done({
            "status": "ok",
            "paper_id": paper_id,
            "paper_type": summary.get("paper_type"),
            "n_contributions": len(summary.get("contributions") or []),
            "n_open_threads": len(summary.get("open_threads") or []),
            "fields_revised": sorted(changed.keys()) if use_patch else None,
        })
