"""gap · extractor subagent (Stage 4): one stateless extraction pass.

Single-paper, grounded extraction, no tool calling: operates over whatever
sections/summary the gap subgraph's `enter` node already read out of paper.json.
`run_extract()` makes one LLM call given whatever loop context the caller hands
it (already-found / rejected / needs-a-better-anchor / missing-coverage-hints)
and returns validated, anchor-grounded candidates. It knows nothing about
looping or merging across passes — that's the gap subgraph's job, since only
the loop holds cross-pass state.

It runs inside the `extractor` graph node, so its LLM call and its per-candidate
deterministic grounding leaves nest under that node in the dev UI's run tree.
"""
from __future__ import annotations

import hashlib

from src.common.utils.prompts import load_prompt
from src.common.utils.trace import emit as emit_trace
from src.llm.registry import chat_with_structured_output, get_llm
from src.common.schemas.gap import GAP_TYPES, LLMGapList
from ...tools.grounding import find_anchor
from ...tools.sections import DEFAULT_SECTION_CHAR_LIMIT, truncate

SYSTEM_PROMPT = load_prompt(__file__)


def gap_id(paper_id: str, canonical_description: str) -> str:
    seed = f"{paper_id}:{canonical_description.strip().lower()}"
    return "gap_" + hashlib.sha1(seed.encode()).hexdigest()[:8]


def build_schema_instructions(available_sections: list[str]) -> str:
    return LLMGapList.prompt_schema(available_sections)


def _format_seeds(label: str, items) -> str | None:
    if not items:
        return None
    bullets = "\n".join(f"- {x}" for x in items if x)
    if not bullets:
        return None
    return f"{label}:\n{bullets}"


def _format_loop_list(label: str, items: list[dict], reason_key: str | None = None) -> str | None:
    """Bullet a list of {"canonical_description": ..., maybe "reason": ...}."""
    if not items:
        return None
    bullets = []
    for it in items:
        canon = it.get("canonical_description") or it.get("title") or ""
        if not canon:
            continue
        if reason_key and it.get(reason_key):
            bullets.append(f"- {canon} ({it[reason_key]})")
        else:
            bullets.append(f"- {canon}")
    if not bullets:
        return None
    return f"{label}:\n" + "\n".join(bullets)


def _format_retry_anchor(items: list[dict]) -> str | None:
    """Full prior fields for each gap that just needs a better anchor — not
    just its canonical_description. Without this the model has to
    reconstruct title/description/type from a bare dedup string and ends up
    regenerating the gap instead of fixing the one thing that was wrong."""
    if not items:
        return None
    blocks = []
    for it in items:
        canon = it.get("canonical_description") or ""
        if not canon:
            continue
        blocks.append(
            f"- title: {it.get('title', '')}\n"
            f"  description: {it.get('description', '')}\n"
            f"  type: {it.get('type', '')}\n"
            f"  canonical_description: {canon}\n"
            f"  section: {it.get('section', '')}\n"
            f"  previous anchor (rejected): {it.get('anchor', '')!r}\n"
            f"  why it failed: {it.get('reason', '')}"
        )
    if not blocks:
        return None
    return ("Needs a better anchor — re-emit this EXACT gap (same title, "
           "description, type, canonical_description, section) with only the "
           "anchor corrected:\n" + "\n".join(blocks))


def _format_needs_revision(items: list[dict]) -> str | None:
    """Full prior fields for each gap a critic flagged a `minor` issue
    against — same idea as `_format_retry_anchor` but for content, not
    grounding: the model is asked to keep canonical_description/section/
    anchor unchanged and only fix title/description/type per `reason`."""
    if not items:
        return None
    blocks = []
    for it in items:
        canon = it.get("canonical_description") or ""
        if not canon:
            continue
        blocks.append(
            f"- title: {it.get('title', '')}\n"
            f"  description: {it.get('description', '')}\n"
            f"  type: {it.get('type', '')}\n"
            f"  canonical_description: {canon}\n"
            f"  section: {it.get('section', '')}\n"
            f"  anchor (keep unchanged): {it.get('anchor', '')!r}\n"
            f"  reviewer's reason: {it.get('reason', '')}"
        )
    if not blocks:
        return None
    return ("Needs revision — re-emit this gap with the SAME "
           "canonical_description, section, and anchor, fixing only "
           "title/description/type per the reviewer's reason:\n" + "\n".join(blocks))


def build_user_prompt(title: str, abstract: str | None,
                      summary: dict,
                      all_sections: dict[str, str],
                      inline_section_names: list[str],
                      category: dict | None = None,
                      already_found: list[dict] | None = None,
                      rejected: list[dict] | None = None,
                      retry_anchor: list[dict] | None = None,
                      needs_revision: list[dict] | None = None,
                      missing_hints: list[str] | None = None) -> str:
    parts: list[str] = []

    if category:
        cat_name = category.get("name") or category.get("category_slug") or ""
        cat_desc = category.get("description") or ""
        block = f"# Workspace category\nName: {cat_name}"
        if cat_desc:
            block += f"\nScope: {cat_desc}"
        block += ("\n\nUse the scope only for framing. Do NOT invent gaps to fit "
                  "the scope — only extract what the paper itself states.")
        parts.append(block + "\n")

    parts.append(f"# Paper\nTitle: {title}")
    if abstract:
        parts.append(f"Abstract: {truncate(abstract, 3000)}")

    # Seeds from the summary stage
    seed_blocks = []
    for label, key_path in [
        ("Author-stated limitations", ("limitations_stated",)),
        ("Open threads / future work", ("open_threads",)),
        ("Approach assumptions", ("approach", "assumptions")),
        ("Prior-work limitations", ("problem", "prior_limitations")),
    ]:
        cur = summary
        for k in key_path:
            cur = (cur or {}).get(k) if isinstance(cur, dict) else None
        block = _format_seeds(label, cur if isinstance(cur, list) else None)
        if block:
            seed_blocks.append(block)
    if seed_blocks:
        parts.append("\n# Candidate seeds (from the summary stage)\n"
                     "Use as starting points, then ground each in the sections "
                     "below. Discard a seed only if no anchor in what you were "
                     "given backs it.\n\n" + "\n\n".join(seed_blocks))

    # Loop context — only present on 2nd+ passes.
    loop_blocks = [
        b for b in [
            _format_loop_list("Gaps already found (do not re-propose)", already_found or []),
            _format_loop_list("Rejected (checked and found invalid — do not propose again)",
                              rejected or [], reason_key="reason"),
            _format_retry_anchor(retry_anchor or []),
            _format_needs_revision(needs_revision or []),
        ] if b
    ]
    if missing_hints:
        loop_blocks.append("Possibly missing (a reviewer's hints — verify before reporting):\n"
                           + "\n".join(f"- {h}" for h in missing_hints))
    if loop_blocks:
        parts.append("\n# Loop context (this is a follow-up pass)\n" + "\n\n".join(loop_blocks))

    # Inlined sections — the only material this agent can see.
    parts.append("\n# Sections inlined for you")
    any_section = False
    for name in inline_section_names:
        text = all_sections.get(name)
        if not text:
            continue
        any_section = True
        text = truncate(text, DEFAULT_SECTION_CHAR_LIMIT)
        parts.append(f"\n## {name}\n{text}")
    if not any_section:
        parts.append("\n(No sections available besides abstract/related_work. Use the "
                     "candidate seeds above only if you can ground them — otherwise "
                     "return an empty gaps list.)")

    parts.append("\n---\n" + build_schema_instructions(inline_section_names))
    return "\n".join(parts)


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
    result = chat_with_structured_output(get_llm("reasoner"), SYSTEM_PROMPT, user_prompt, LLMGapList).model_dump()
    raw_candidates = result.get("gaps") or []
    emit_trace({"type": "event", "name": "extractor pass",
                "data": {"n_candidates": len(raw_candidates)}})

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
        match = find_anchor(section_text, anchor)
        emit_trace({"type": "tool", "name": "grounding",
                    "data": {"gap_id": gid, "section": section, "anchor": anchor,
                             "status": match["status"], "quote": match.get("quote"),
                             "char_offset": match.get("char_offset"),
                             "ambiguous_offsets": match.get("offsets")}})
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

    return {"raw_count": len(raw_candidates), "candidates": candidates, "warnings": warnings}
