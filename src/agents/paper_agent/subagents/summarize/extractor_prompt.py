"""Prompt construction for the summarize subagent's extractor (Stage 3).

We feed only a curated subset of sections (see PAPER_AND_GAP_AGENT.md Stage 3)
and ask for a single JSON object matching the documented schema.
"""
from __future__ import annotations

import json

from .....common.schemas.summary import PaperSummary, PaperSummaryPatch

# Canonical sections in priority order. `results` is truncated to first paragraph.
PRIORITY_SECTIONS = [
    "abstract",
    "introduction",
    "method",
    "results",
    "discussion",
    "limitations",
    "conclusion",
    "future_work",
]

# Char budget per section. This is the only stage that reads the paper's
# actual text (gap extraction re-reads it too, but summarize runs first and
# everything downstream — gap seeds, idea generation — traces back to what
# made it into the summary), so these are deliberately generous: a safety
# net against a pathological outlier section, not a routine cutoff. A real
# failure mode this caused at the old, tighter limits: `method` truncated at
# 8000 chars cut off a paper's positional-encoding subsection, so the model
# wrote about it from background knowledge while the critic flagged it as
# unsupported — neither side could see the part of the source that would
# have settled it.
PER_SECTION_CHARS = {
    "abstract": 6000,
    "introduction": 14000,
    "method": 24000,
    "results": 3000,        # first paragraph only
    "discussion": 10000,
    "limitations": 8000,
    "conclusion": 6000,
    "future_work": 6000,
}


SYSTEM_PROMPT = """You are a research-paper summarization agent for a long-running \
research-ideation workspace. Your output is consumed by downstream agents \
(a GapAgent that hunts research gaps, and an IdeaAgent that proposes new \
research directions), so structure and factual fidelity matter more than prose.

Rules:
- Output a single JSON object matching the requested schema exactly. No prose \
outside the JSON.
- Be FACTUAL: only state what the paper itself says or directly implies. Do \
not speculate or add outside knowledge.
- Separate `assumptions` (what the paper takes for granted) from \
`limitations_stated` (what the authors explicitly acknowledge as weak).
- For theory/survey/position papers, `datasets` and `metrics` may be empty arrays.
- The paper may come from any field (CS, physics, biology, medicine, psychology, \
chemistry, social science, etc.). Use field-appropriate vocabulary in free-string \
fields — do not force machine-learning terminology onto non-ML papers.
- Keep `context_summary` between 150 and 250 words.
- Keep individual list items concise (one sentence each)."""


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + " […truncated]"


def _first_paragraph(text: str) -> str:
    for sep in ("\n\n", "\n"):
        idx = text.find(sep)
        if idx > 0:
            return text[:idx]
    return text


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
        parts.append("\n## abstract\n" + _truncate(abstract, PER_SECTION_CHARS["abstract"]))

    for name in PRIORITY_SECTIONS:
        text = sections.get(name)
        if not text:
            continue
        if name == "results":
            text = _first_paragraph(text)
        text = _truncate(text, PER_SECTION_CHARS[name])
        parts.append(f"\n## {name}\n{text}")

    if patch_fields:
        parts.append("\n---\n" + PaperSummaryPatch.prompt_schema(patch_fields))
    else:
        parts.append("\n---\n" + PaperSummary.prompt_schema())
    return "\n".join(parts)
