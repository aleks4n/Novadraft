"""Prompt construction for the summarize subagent's critic (Stage 3b).

The critic only judges an existing `summary` against the source sections —
it never rewrites. Reuses the same section selection/truncation as the
extractor so the critic sees the same material that produced the summary,
not a different view of the paper.
"""
from __future__ import annotations

import json

from .....common.schemas.summary_verify import SummaryCritique
from .extractor_prompt import PER_SECTION_CHARS, PRIORITY_SECTIONS, _first_paragraph, _truncate

SYSTEM_PROMPT = """You are a faithfulness/completeness critic for a research-paper \
summarization pipeline. You are given the paper's source sections and a structured \
summary an upstream agent produced from them. Your only job is to judge the summary \
against the source — you never rewrite or improve it yourself.

Rules:
- Output a single JSON object matching the requested schema exactly. No prose outside \
the JSON.
- Flag a `major` issue only for: a claim not supported by (or contradicted by) the \
source text, or a missing item that materially changes the paper's framing (e.g. a \
dropped key contribution, an assumption stated as fact).
- Flag a `minor` issue for: imprecise phrasing, a missing nuance/caveat, a borderline \
field-type choice — anything that wouldn't mislead a downstream reader.
- Do not flag stylistic preferences (length, tone) as issues.
- Report a `missing_hints` entry only if the source states something — a contribution, \
dataset, metric, limitation, or open thread — that no field in the summary captures at \
all. This is different from an issue: an issue is about a field that exists but is wrong \
or imprecise; a missing hint is about something not reflected ANYWHERE. Don't double-count \
the same gap as both.
- If the summary is faithful and reasonably complete, return status "pass" with empty \
`issues` and `missing_hints` — do not invent either to fill the lists.
- If you are given an "Open issues from earlier rounds" section, check each of those items \
(by its `id`) against the current summary below first: if it's resolved, omit it from your \
output entirely; if it's still present, re-list it with the SAME `id`. Any issue you report \
without an `id` is treated as brand new, so never invent an `id` for a genuinely new issue.
- If you are given an "Open missing items from earlier rounds" section, apply the same rule \
to `missing_hints`: omit a hint (by its `id`) once it's reflected somewhere in the summary; \
re-list it with the SAME `id` if still missing.
- After checking the open lists above, still do a fresh, full read of the summary against \
the source for anything else — don't limit yourself to re-checking what was already \
flagged."""


def _format_open_issues(open_issues: list[dict] | None) -> str | None:
    if not open_issues:
        return None
    lines = ["# Open issues from earlier rounds\nThese were flagged against an earlier "
             "draft of this summary, which has since been revised. Verify each one against "
             "the current summary below — only re-list it (with its id) if it's still "
             "actually present; otherwise leave it out.\n"]
    for issue in open_issues:
        lines.append(f"- [id={issue['id']}, {issue['field']}, {issue['severity']}] "
                     f"{issue['description']}")
    return "\n".join(lines)


def _format_open_hints(open_hints: list[dict] | None) -> str | None:
    if not open_hints:
        return None
    lines = ["# Open missing items from earlier rounds\nA prior round judged these "
             "possibly-missing. The summary below may have already been revised to "
             "address some of them — only re-list it (with its id) if still missing.\n"]
    for hint in open_hints:
        lines.append(f"- [id={hint['id']}] {hint['hint']}")
    return "\n".join(lines)


def build_critic_prompt(sections: dict[str, str], summary: dict,
                        open_issues: list[dict] | None = None,
                        open_hints: list[dict] | None = None) -> str:
    parts: list[str] = ["# Source sections"]
    for name in PRIORITY_SECTIONS:
        text = sections.get(name)
        if not text:
            continue
        if name == "results":
            text = _first_paragraph(text)
        text = _truncate(text, PER_SECTION_CHARS[name])
        parts.append(f"\n## {name}\n{text}")

    issues_block = _format_open_issues(open_issues)
    if issues_block:
        parts.append("\n---\n" + issues_block)
    hints_block = _format_open_hints(open_hints)
    if hints_block:
        parts.append("\n---\n" + hints_block)

    parts.append("\n---\n# Summary to verify\n" + json.dumps(summary, indent=2, ensure_ascii=False))
    parts.append("\n---\n" + SummaryCritique.prompt_schema())
    return "\n".join(parts)
