"""GapAgent entry point (Stage 4).

Single-paper, grounded extraction:
- Reads `content.sections` (limitations/discussion/future_work/conclusion) plus
  `summary` seeds from paper.json.
- Calls the LLM once to produce candidate gaps with verbatim quotes.
- Writes each candidate to `workspace/<cat>/gaps/pending/<gap_id>.json` with
  status="unchecked".
- Writes the new ids into `paper.json::gaps.gap_ids` and flips
  `processing.gaps_extracted`.

Out of scope (handled later by GapCheckAgent):
- Cross-paper dedup
- Online "is this already solved?" check
- Severity / novelty judgment
- Promotion from pending/ to verified gaps
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from .....common.utils import storage

_NOVADRAFT_ROOT = Path(__file__).resolve().parents[5]
if str(_NOVADRAFT_ROOT) not in sys.path:
    sys.path.insert(0, str(_NOVADRAFT_ROOT))

from src.llm.registry import chat_json_with_tools  # noqa: E402
from .prompt import (
    SYSTEM_PROMPT,
    GAP_TYPES,
    build_user_prompt,
    get_section_tool_spec,
    select_gap_sections,
)

# Cap per tool call so a runaway tool loop can't blow up the context.
_TOOL_SECTION_CHAR_LIMIT = 12000


class PaperNotFoundError(Exception):
    pass


class NotSummarizedError(Exception):
    pass


# --- storage helpers ---------------------------------------------------------

def _pending_dir(category_slug: str) -> Path:
    d = storage.category_dir(category_slug) / "gaps" / "pending"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _gap_id(paper_id: str, canonical_description: str) -> str:
    seed = f"{paper_id}:{canonical_description.strip().lower()}"
    return "gap_" + hashlib.sha1(seed.encode()).hexdigest()[:8]


def _read_category_meta(category_slug: str) -> dict | None:
    path = storage.category_dir(category_slug) / "meta.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _write_paper(category_slug: str, record: dict) -> None:
    pdir = storage.paper_dir(category_slug, record["paper_id"])
    (pdir / "paper.json").write_text(
        json.dumps(record, indent=2, ensure_ascii=False)
    )


# --- evidence quote grounding ------------------------------------------------

def _find_offset(haystack: str, needle: str) -> list[int] | None:
    """Best-effort: locate the verbatim quote in the section text.

    Returns [start, end] if found. None if not — we still keep the quote, but
    callers can log a warning so the LLM's grounding can be inspected later.
    """
    if not needle:
        return None
    idx = haystack.find(needle)
    if idx >= 0:
        return [idx, idx + len(needle)]
    # Soft fallback: collapse whitespace and try again.
    norm_h = " ".join(haystack.split())
    norm_n = " ".join(needle.split())
    idx = norm_h.find(norm_n)
    if idx >= 0:
        return [idx, idx + len(norm_n)]
    return None


# --- main --------------------------------------------------------------------

def extract_gaps(category_slug: str, paper_id: str, force: bool = False) -> dict:
    record = storage.read_paper_json(category_slug, paper_id)
    if record is None:
        raise PaperNotFoundError(f"{category_slug}/{paper_id}")

    processing = record.setdefault("processing", {})
    if not processing.get("summarized"):
        raise NotSummarizedError(
            f"{paper_id}: run paper_summary first "
            f"(processing.summarized is false)"
        )
    if processing.get("gaps_extracted") and not force:
        existing = ((record.get("gaps") or {}).get("gap_ids")) or []
        return {
            "status": "skipped",
            "paper_id": paper_id,
            "reason": "already_extracted",
            "gap_ids": existing,
        }

    sections = ((record.get("content") or {}).get("sections")) or {}
    summary = record.get("summary") or {}
    metadata = record.get("metadata") or {}

    all_section_names = list(sections.keys())
    inline_sections = select_gap_sections(all_section_names)

    user_prompt = build_user_prompt(
        title=metadata.get("title") or "(unknown)",
        abstract=metadata.get("abstract"),
        summary=summary,
        all_sections=sections,
        inline_section_names=inline_sections,
        category=_read_category_meta(category_slug),
    )

    fetched_sections: list[dict] = []

    def _get_section(args: dict) -> str:
        name = (args.get("name") or "").strip()
        reason = (args.get("reason") or "").strip()
        text = sections.get(name)
        fetched_sections.append({"name": name, "reason": reason,
                                 "present": bool(text)})
        if not text:
            return (f"not available: section '{name}' is not present in this "
                    f"paper. Available sections: {', '.join(all_section_names)}")
        if len(text) > _TOOL_SECTION_CHAR_LIMIT:
            return text[:_TOOL_SECTION_CHAR_LIMIT].rstrip() + " […truncated]"
        return text

    result, tool_trace = chat_json_with_tools(
        SYSTEM_PROMPT,
        user_prompt,
        tools=[get_section_tool_spec(all_section_names)],
        tool_handlers={"get_section": _get_section},
    )
    candidates = result.get("gaps") or []

    warnings: list[str] = []
    pending_dir = _pending_dir(category_slug)
    gap_ids: list[str] = []
    seen: set[str] = set()

    for cand in candidates:
        canonical = (cand.get("canonical_description") or "").strip()
        title = (cand.get("title") or "").strip()
        description = (cand.get("description") or "").strip()
        gtype = (cand.get("type") or "").strip()
        section = (cand.get("evidence_section") or "").strip()
        quote = (cand.get("quote") or "").strip()

        if not (canonical and description and quote and section):
            warnings.append(
                f"gap_dropped: missing required field "
                f"(canonical={bool(canonical)}, description={bool(description)}, "
                f"quote={bool(quote)}, section={bool(section)})"
            )
            continue
        if gtype not in GAP_TYPES:
            warnings.append(f"gap_type_unknown: '{gtype}' -> coerced to 'limitation'")
            gtype = "limitation"
        if section not in sections:
            warnings.append(
                f"gap_evidence_section_unknown: '{section}' not in this paper "
                f"(available: {', '.join(sections.keys())})"
            )

        gid = _gap_id(paper_id, canonical)
        if gid in seen:
            # Same canonical_description twice from the same paper — skip dup.
            continue
        seen.add(gid)

        section_text = sections.get(section, "")
        char_offset = _find_offset(section_text, quote)
        if char_offset is None:
            warnings.append(
                f"gap_quote_not_grounded: gid={gid} section={section} "
                f"(quote not found verbatim in section text)"
            )

        gap_record = {
            "gap_id": gid,
            "status": "unchecked",
            "title": title,
            "description": description,
            "type": gtype,
            "canonical_description": canonical,
            "evidence": [
                {
                    "paper_id": paper_id,
                    "section": section,
                    "quote": quote,
                    "char_offset": char_offset,
                }
            ],
            "extracted_at": storage.now_iso(),
        }
        (pending_dir / f"{gid}.json").write_text(
            json.dumps(gap_record, indent=2, ensure_ascii=False)
        )
        gap_ids.append(gid)

    # Update paper.json
    record["gaps"] = {"gap_ids": gap_ids}
    processing["gaps_extracted"] = True
    if fetched_sections:
        names = ", ".join(f["name"] for f in fetched_sections)
        warnings.insert(0, f"gap_agent_fetched_sections: {names}")
    if warnings:
        record.setdefault("warnings", []).extend(warnings)
    _write_paper(category_slug, record)

    return {
        "status": "ok",
        "paper_id": paper_id,
        "n_gaps": len(gap_ids),
        "gap_ids": gap_ids,
        "fetched_sections": fetched_sections,
        "tool_trace": tool_trace,
        "warnings": warnings,
    }
