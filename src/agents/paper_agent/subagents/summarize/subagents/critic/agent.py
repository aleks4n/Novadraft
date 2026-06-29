"""summarize · critic subagent (Stage 3b): one stateless critique pass.

Runs two checks against the paper's *current* `paper.json::summary`:
  1. Grounding check (deterministic) — do the metric values / dataset names /
     headline numbers actually appear in `content.sections`? (see
     `summarize/tools/grounding.py`)
  2. Critic check (single LLM call) — does the summary faithfully and
     completely represent the source sections?

`run_critic()` only judges — it never regenerates the summary and never
writes to paper.json. The extractor↔critic cycle that decides whether to
regenerate lives in `summarize/agent.py`.

It runs inside the `critic` graph node, so its LLM call and its deterministic
grounding leaf show up nested under that node in the dev UI's run tree.
"""
from __future__ import annotations

from src.common.utils import storage
from src.common.utils.issue_tracking import merge_items
from src.common.utils.prompts import load_prompt
from src.common.utils.logging.trace import emit as emit_trace
from src.llm.registry import chat_with_structured_output, get_llm
from src.common.schemas.summary_verify import SummaryCritique
from ...tools.grounding import check_grounding
from ...tools.sections import (
    PER_SECTION_CHARS, PRIORITY_SECTIONS, first_paragraph, truncate,
)

import json

SYSTEM_PROMPT = load_prompt(__file__)

_ISSUE_FIELDS = ["field", "severity", "description", "evidence_section"]
_HINT_FIELDS = ["hint"]


class PaperNotFoundError(Exception):
    pass


class NotSummarizedError(Exception):
    pass


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
            text = first_paragraph(text)
        text = truncate(text, PER_SECTION_CHARS[name])
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


def run_critic(category_slug: str, paper_id: str, persisted_issues: list[dict] | None = None,
               persisted_hints: list[dict] | None = None, round_num: int = 1) -> dict:
    """Judge the paper's current summary. Reads fresh from paper.json each
    call, so a caller looping extractor -> critic always critiques the
    latest summary, not a stale copy.

    `persisted_issues`/`persisted_hints` (optional): the full, ever-growing
    lists carried by the caller (`summarize/agent.py`) — including items
    already marked `resolved` from earlier rounds, possibly from an earlier
    top-level `summarize()` call entirely. Only each list's still-open items
    are shown to the LLM (with their `id`s) for it to confirm or drop;
    resolved items need no re-checking. This call merges the LLM's
    single-round verdict back into both lists (never removing anything, only
    flipping `resolved`/appending new items) and returns the updated lists.

    Returns `{"status": "pass"|"issues", "grounding": {"checked", "unmatched"},
    "issues": [...], "missing_hints": [...]}` — both lists are the full
    updated persisted lists.
    """
    persisted_issues = [dict(i) for i in (persisted_issues or [])]
    persisted_hints = [dict(h) for h in (persisted_hints or [])]

    record = storage.read_paper_json(category_slug, paper_id)
    if record is None:
        raise PaperNotFoundError(f"{category_slug}/{paper_id}")

    processing = record.get("processing") or {}
    if not processing.get("summarized"):
        raise NotSummarizedError(
            f"{paper_id}: run the extractor first (processing.summarized is false)"
        )

    sections = ((record.get("content") or {}).get("sections")) or {}
    summary = record.get("summary") or {}

    grounding = check_grounding(summary, sections)
    emit_trace({"type": "tool", "name": "grounding",
                "data": {"checked": grounding["checked"], "unmatched": grounding["unmatched"]}})

    open_issues = [i for i in persisted_issues if not i["resolved"]]
    open_hints = [h for h in persisted_hints if not h["resolved"]]
    user_prompt = build_critic_prompt(sections, summary, open_issues=open_issues,
                                      open_hints=open_hints)
    critique = chat_with_structured_output(get_llm("reasoner"), SYSTEM_PROMPT, user_prompt, SummaryCritique).model_dump()

    issues = merge_items(persisted_issues, critique.get("issues") or [], round_num,
                         _ISSUE_FIELDS)
    hints = merge_items(persisted_hints, critique.get("missing_hints") or [], round_num,
                        _HINT_FIELDS)
    n_open_issues = len([i for i in issues if not i["resolved"]])
    n_open_hints = len([h for h in hints if not h["resolved"]])

    return {
        "status": "issues" if (grounding["unmatched"] or n_open_issues or n_open_hints)
                  else "pass",
        "grounding": grounding,
        "issues": issues,
        "missing_hints": hints,
    }
