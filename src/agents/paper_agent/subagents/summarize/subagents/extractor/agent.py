"""summarize · extractor subagent (Stage 3): one stateless LLM pass.

Reads `content.sections` from paper.json, calls the LLM once with a structured
JSON-output prompt, and writes the result to `paper.json::summary`. Called
repeatedly by the summarize subgraph's extractor↔critic cycle (see
`summarize/agent.py`) on a regenerate round, with `feedback` from the critic
*and* the previous summary itself appended to the prompt.

On a feedback round the LLM is constrained to `PaperSummaryPatch` — only the
top-level fields named by the critique's `field` paths — and the result is
merged into the existing summary in code (`dict.update`), not written
wholesale. A field no issue mentioned is therefore never touched, regardless
of what the model does or doesn't echo back.

`run_extract` is a pure pass: no graph/loop knowledge. It runs inside the
`extractor` graph node, so the LLM call it makes (via `src/llm/registry.py`)
and the informational events it emits show up nested under that node in the
dev UI's run tree automatically.
"""
from __future__ import annotations

import json

from pydantic import ValidationError

from src.common.utils import storage
from src.common.utils.prompts import load_prompt
from src.common.utils.trace import emit as emit_trace
from src.llm.registry import chat_with_structured_output, get_llm
from src.common.schemas.summary import PaperSummary, PaperSummaryPatch
from ...tools.sections import (
    PER_SECTION_CHARS, PRIORITY_SECTIONS, first_paragraph, truncate,
)

SYSTEM_PROMPT = load_prompt(__file__)

_PATCH_FIELD_NAMES = set(PaperSummaryPatch.model_fields.keys())


class PaperNotFoundError(Exception):
    pass


class NotExtractedError(Exception):
    pass


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


def build_user_prompt(title: str, authors: list[str], year: int | None,
                      abstract: str | None, sections: dict[str, str],
                      category: dict | None = None,
                      feedback: list[dict] | None = None,
                      previous_summary: dict | None = None,
                      patch_fields: list[str] | None = None,
                      missing_hints: list[str] | None = None) -> str:
    parts: list[str] = []
    if feedback:
        if patch_fields:
            lines = ["# Revision needed\nA verifier reviewed your previous summary of this "
                     "paper (given below) and found the issues listed below. You will return "
                     "ONLY the revised top-level fields named in each issue — every other "
                     "field is kept as-is by the caller, you don't need to (and shouldn't) "
                     "repeat them. Re-read the relevant sections rather than guessing at a "
                     "fix.\n"]
        else:
            lines = ["# Revision needed\nA verifier reviewed your previous summary of this "
                     "paper (given below) and found the issues listed below, AND judged that "
                     "something the source states isn't reflected anywhere in the summary "
                     "(see \"Possibly missing\" below) — so this round asks for the FULL "
                     "summary object back, not a narrow patch, since the missing content "
                     "may belong in a field no issue named. Fix the listed issues, add "
                     "whatever the missing-content hints point to in whichever field it "
                     "actually belongs, and otherwise keep fields unchanged from your "
                     "previous summary below.\n"]
        for issue in feedback:
            field = issue.get("field", "?")
            desc = issue.get("description", "")
            section = issue.get("evidence_section")
            line = f"- [{field}] {desc}"
            if section:
                line += f" (see section: {section})"
            lines.append(line)
        parts.append("\n".join(lines) + "\n")
        if previous_summary:
            parts.append("\n# Your previous summary (for context only — revise only the "
                         "fields the issues above name)\n"
                         + json.dumps(previous_summary, indent=2, ensure_ascii=False) + "\n")
    elif missing_hints and previous_summary:
        parts.append("\n# Your previous summary (for context only — keep fields unchanged "
                     "except where the missing-content hints below point to something new)\n"
                     + json.dumps(previous_summary, indent=2, ensure_ascii=False) + "\n")
    if missing_hints:
        parts.append("\n# Possibly missing\nA verifier judged that something the source "
                     "states isn't reflected anywhere in the current summary. Use these "
                     "hints to look again, but only add it if you find real support for it "
                     "in the sections below:\n"
                     + "\n".join(f"- {h}" for h in missing_hints) + "\n")
    if category:
        cat_name = category.get("name") or category.get("category_slug") or ""
        cat_desc = category.get("description") or ""
        block = f"# Workspace category\nName: {cat_name}"
        if cat_desc:
            block += f"\nScope: {cat_desc}"
        block += ("\n\nThis paper is being added to the above category. Frame the "
                  "summary so it is maximally useful for downstream gap analysis and "
                  "idea generation *within this category's scope*. Lean toward "
                  "keywords/topics/contributions that are relevant to this scope, "
                  "without inventing claims the paper does not make.")
        parts.append(block + "\n")
    parts.append(f"# Paper\nTitle: {title}")
    if authors:
        parts.append("Authors: " + ", ".join(authors[:8]) + ("…" if len(authors) > 8 else ""))
    if year:
        parts.append(f"Year: {year}")
    if abstract and "abstract" not in sections:
        parts.append("\n## abstract\n" + truncate(abstract, PER_SECTION_CHARS["abstract"]))

    for name in PRIORITY_SECTIONS:
        text = sections.get(name)
        if not text:
            continue
        if name == "results":
            text = first_paragraph(text)
        text = truncate(text, PER_SECTION_CHARS[name])
        parts.append(f"\n## {name}\n{text}")

    if patch_fields:
        parts.append("\n---\n" + PaperSummaryPatch.prompt_schema(patch_fields))
    else:
        parts.append("\n---\n" + PaperSummary.prompt_schema())
    return "\n".join(parts)


def run_extract(category_slug: str, paper_id: str, force: bool = False,
                feedback: list[dict] | None = None,
                missing_hints: list[str] | None = None) -> dict:
    """One extractor pass.

    `feedback` (optional) is a list of critic issues (see the critic subagent)
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
    record = storage.read_paper_json(category_slug, paper_id)
    if record is None:
        raise PaperNotFoundError(f"{category_slug}/{paper_id}")

    processing = record.setdefault("processing", {})
    if not processing.get("extracted"):
        raise NotExtractedError(
            f"{paper_id}: run paper_extract first (processing.extracted is false)"
        )
    if processing.get("summarized") and not force and not feedback and not missing_hints:
        emit_trace({"type": "event", "name": "already summarized, skipping",
                    "data": {"paper_id": paper_id}})
        return {"status": "skipped", "paper_id": paper_id, "reason": "already_summarized"}

    sections = ((record.get("content") or {}).get("sections")) or {}
    if not sections:
        emit_trace({"type": "event", "name": "no sections to summarize, skipping",
                    "data": {"paper_id": paper_id}})
        record.setdefault("warnings", []).append("summary_skipped: no sections")
        _write(category_slug, record)
        return {"status": "skipped", "paper_id": paper_id, "reason": "no_sections"}

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
    try:
        parsed = chat_with_structured_output(get_llm("reasoner"), SYSTEM_PROMPT, user_prompt, schema)
    except (ValidationError, ValueError) as exc:
        # A failed structured-output call (no parseable response, or one that
        # won't validate even after the registry's tool-call retry) shouldn't
        # abort the whole run. Fall into the empty-response handling below,
        # which keeps any previously good summary intact — this matters most on
        # a feedback/missing-hints regenerate round against an already-good
        # summary, where the summarize graph then re-critiques it unchanged.
        emit_trace({"type": "event", "name": "structured-output call failed",
                    "data": {"paper_id": paper_id, "error": str(exc)}})
        parsed = None
    raw = parsed.model_dump() if parsed is not None else {}

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
        emit_trace({"type": "event", "name": "empty LLM response, keeping previous summary",
                    "data": {"paper_id": paper_id}})
        record.setdefault("warnings", []).append("summary_failed: empty LLM response")
        _write(category_slug, record)
        return {"status": "error", "paper_id": paper_id, "reason": "empty_llm_response"}

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

    return {
        "status": "ok",
        "paper_id": paper_id,
        "paper_type": summary.get("paper_type"),
        "n_contributions": len(summary.get("contributions") or []),
        "n_open_threads": len(summary.get("open_threads") or []),
        "fields_revised": sorted(changed.keys()) if use_patch else None,
    }
