"""gap subagent — extractor (Stage 4): one stateless extraction pass.

Single-paper, grounded extraction, no tool calling: operates over whatever
sections/summary `gap.prepare_context` already read out of paper.json.
`run_extract()` makes one LLM call given whatever loop context the caller
hands it (already-found / rejected / needs-a-better-anchor / missing-
coverage-hints) and returns validated, anchor-grounded candidates. It knows
nothing about looping or merging across passes — that's `gap.extract_gaps`'s
job, since only the loop holds cross-pass state.
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

from .....common.utils.runlog import log_event, step
from .....common.schemas.gap import GAP_TYPES, LLMGapList

_NOVADRAFT_ROOT = Path(__file__).resolve().parents[5]
if str(_NOVADRAFT_ROOT) not in sys.path:
    sys.path.insert(0, str(_NOVADRAFT_ROOT))

from src.llm.registry import chat_json  # noqa: E402
from .extractor_prompt import SYSTEM_PROMPT, build_user_prompt  # noqa: E402
from .grounding import find_anchor  # noqa: E402


def gap_id(paper_id: str, canonical_description: str) -> str:
    seed = f"{paper_id}:{canonical_description.strip().lower()}"
    return "gap_" + hashlib.sha1(seed.encode()).hexdigest()[:8]


def _validate_candidate(cand: dict, sections: dict[str, str],
                         rejected_lower: set[str], warnings: list[str]) -> dict | None:
    """Coerce/validate one raw LLM candidate. Returns None if it's dropped
    outright (missing fields, unknown section, or it's a previously-rejected
    gap re-proposed).
    """
    canonical = (cand.get("canonical_description") or "").strip()
    title = (cand.get("title") or "").strip()
    description = (cand.get("description") or "").strip()
    gtype = (cand.get("type") or "").strip()
    section = (cand.get("evidence_section") or "").strip()
    anchor = (cand.get("anchor") or "").strip()

    if not (canonical and description and anchor and section):
        warnings.append(
            f"gap_dropped: missing required field "
            f"(canonical={bool(canonical)}, description={bool(description)}, "
            f"anchor={bool(anchor)}, section={bool(section)})"
        )
        return None
    if canonical.lower() in rejected_lower:
        return None
    if gtype not in GAP_TYPES:
        warnings.append(f"gap_type_unknown: '{gtype}' -> coerced to 'limitation'")
        gtype = "limitation"
    if section not in sections:
        warnings.append(
            f"gap_evidence_section_unknown: '{section}' not in this paper "
            f"(available: {', '.join(sections.keys())})"
        )
        return None

    return {
        "title": title, "description": description, "type": gtype,
        "canonical_description": canonical, "section": section, "anchor": anchor,
    }


def run_extract(ctx: dict, loop_context: dict | None = None) -> dict:
    """One extractor LLM pass. `loop_context` (optional) carries what earlier
    passes already learned:
      {"already_found": [{"canonical_description": ...}, ...],
       "rejected": [{"canonical_description": ..., "reason": ...}, ...],
       "retry_anchor": [{"canonical_description": ..., "reason": ...}, ...],
       "needs_revision": [{"canonical_description": ..., "reason": ...}, ...],
       "missing_hints": [str, ...]}

    Returns `{"raw_count": int, "candidates": [...], "warnings": [...]}`.
    Each candidate dict has gap_id/title/description/type/
    canonical_description/section/anchor/quote/char_offset/
    grounding_status/ambiguous_offsets — already validated and anchor-
    grounded, but NOT merged against any other pass's output.
    """
    loop_context = loop_context or {}
    with step("extractor") as node:
        user_prompt = build_user_prompt(
            title=ctx["metadata"].get("title") or "(unknown)",
            abstract=ctx["metadata"].get("abstract"),
            summary=ctx["summary"],
            all_sections=ctx["sections"],
            inline_section_names=ctx["inline_sections"],
            category=ctx["category_meta"],
            already_found=loop_context.get("already_found"),
            rejected=loop_context.get("rejected"),
            retry_anchor=loop_context.get("retry_anchor"),
            needs_revision=loop_context.get("needs_revision"),
            missing_hints=loop_context.get("missing_hints"),
        )
        result = chat_json(SYSTEM_PROMPT, user_prompt, response_schema=LLMGapList)
        raw_candidates = result.get("gaps") or []
        log_event("extractor pass", n_candidates=len(raw_candidates))

        rejected_lower = {
            (r.get("canonical_description") or "").lower()
            for r in (loop_context.get("rejected") or [])
        }
        warnings: list[str] = []
        candidates: list[dict] = []
        for cand in raw_candidates:
            valid = _validate_candidate(cand, ctx["sections"], rejected_lower, warnings)
            if valid is None:
                continue

            section = valid["section"]
            anchor = valid["anchor"]
            gid = gap_id(ctx["paper_id"], valid["canonical_description"])
            section_text = ctx["sections"].get(section, "")
            with step("grounding", gap_id=gid, section=section, anchor=anchor) as gnode:
                match = find_anchor(section_text, anchor)
                gnode["data"]["result"] = {
                    "status": match["status"],
                    "quote": match.get("quote"),
                    "char_offset": match.get("char_offset"),
                    "ambiguous_offsets": match.get("offsets"),
                }
            status = match["status"]

            record_status = "unchecked"
            quote, char_offset, ambiguous_offsets = "", None, None
            if status == "matched":
                quote = match["quote"]
                char_offset = match["char_offset"]
            elif status == "ambiguous":
                ambiguous_offsets = match["offsets"]
                record_status = "ambiguous_anchor"

            candidates.append({
                **valid,
                "gap_id": gid,
                "quote": quote,
                "char_offset": char_offset,
                "grounding_status": status,
                "ambiguous_offsets": ambiguous_offsets,
                "record_status": record_status,
            })

        result_dict = {"raw_count": len(raw_candidates), "candidates": candidates,
                       "warnings": warnings}
        node["data"]["result"] = {"raw_count": result_dict["raw_count"],
                                   "n_valid": len(candidates)}
        return result_dict
