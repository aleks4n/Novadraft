"""Prompt construction for the gap subagent's critic (Stage 4b)."""
from __future__ import annotations

import json

from .....common.schemas.gap_verify import GapCritique
from .extractor_prompt import DEFAULT_SECTION_CHAR_LIMIT, _truncate

CRITIC_SYSTEM_PROMPT = """You are a faithfulness critic for a research-gap extraction \
pipeline. You are given a paper's source sections and a list of gap candidates an \
upstream extractor already proposed from them, each backed by a quoted sentence. Your \
only job is to judge those candidates against the source — you never invent or rewrite \
gaps yourself.

Rules:
- Output a single JSON object matching the requested schema exactly. No prose outside \
the JSON.
- Flag a `major` issue on a gap_id only when: the quoted sentence does NOT actually \
support the description (the extractor over-read it), the gap is not something the paper \
itself states (it reads as inference, not an authors' admission), or it isn't actually a \
limitation/future-work/caveat/negative-result/open-problem/assumption at all.
- Flag a `minor` issue for a debatable `type` choice or imprecise title/description that \
doesn't warrant dropping the gap.
- Do NOT flag a gap merely because its anchor was hard to locate — that's handled \
separately, by a deterministic check, not by you.
- Report a `missing_hints` entry only if you can point to a clearly stated gap (a \
limitation, future-work item, caveat, threat to validity, negative result, open problem, \
or explicit assumption) in the given sections that is NOT covered by any candidate — a \
phrase or section name is enough, the extractor will look again, you don't need to write \
the gap yourself.
- If every candidate is sound and nothing looks missing, return status "pass" with empty \
`issues` and `missing_hints` — do not invent either to fill the lists.
- If you are given an "Open issues from earlier rounds" section, check each of those items \
(by its `id`) against the candidates below first: if it's resolved (e.g. the extractor \
revised the gap), omit it from your output entirely; if it's still present, re-list it \
with the SAME `id`. Any issue you report without an `id` is treated as brand new, so never \
invent an `id` for a genuinely new issue.
- If you are given an "Open missing items from earlier rounds" section, apply the same \
rule to `missing_hints`: omit a hint (by its `id`) once it's now covered by a candidate; \
re-list it with the SAME `id` if still missing.
- After checking the open lists above, still scan everything fresh for issues and missing \
coverage — don't limit yourself to repeating the previous round's verdict."""


def _format_open_issues(open_issues: list[dict] | None) -> str | None:
    if not open_issues:
        return None
    lines = ["# Open issues from earlier rounds\nThese were flagged against an earlier "
             "version of these candidates. Verify each one against the candidates below — "
             "only re-list it (with its id) if it's still actually present; otherwise leave "
             "it out.\n"]
    for issue in open_issues:
        lines.append(f"- [id={issue['id']}, gap_id={issue['gap_id']}, {issue['severity']}] "
                     f"{issue['reason']}")
    return "\n".join(lines)


def _format_open_hints(open_hints: list[dict] | None) -> str | None:
    if not open_hints:
        return None
    lines = ["# Open missing items from earlier rounds\nA prior round judged these "
             "possibly-missing gaps. The candidate set below may already include new "
             "candidates addressing some of them — only re-list it (with its id) if still "
             "missing.\n"]
    for hint in open_hints:
        lines.append(f"- [id={hint['id']}] {hint['hint']}")
    return "\n".join(lines)


def build_critic_prompt(sections: dict[str, str], inline_section_names: list[str],
                        candidates: list[dict], open_issues: list[dict] | None = None,
                        open_hints: list[dict] | None = None) -> str:
    parts: list[str] = ["# Source sections"]
    for name in inline_section_names:
        text = sections.get(name)
        if not text:
            continue
        text = _truncate(text, DEFAULT_SECTION_CHAR_LIMIT)
        parts.append(f"\n## {name}\n{text}")

    issues_block = _format_open_issues(open_issues)
    if issues_block:
        parts.append("\n---\n" + issues_block)
    hints_block = _format_open_hints(open_hints)
    if hints_block:
        parts.append("\n---\n" + hints_block)

    parts.append("\n---\n# Gap candidates to verify\n"
                 + json.dumps(candidates, indent=2, ensure_ascii=False))
    parts.append("\n---\n" + GapCritique.prompt_schema())
    return "\n".join(parts)
