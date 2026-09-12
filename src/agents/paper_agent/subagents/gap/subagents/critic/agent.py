"""gap · critic subagent (Stage 4b): one stateless critique pass.

Reviews gap candidates an extractor pass already produced: is each one
genuinely stated (not hallucinated/inferred), does its quote actually support
its description, and does anything clearly stated in the given sections look
missing. Only ever returns a verdict over candidates handed to it — never
invents or rewrites a gap, never merges across passes. That bookkeeping (drop a
flagged gap, decide convergence) is the gap subgraph's job.

Each candidate already carries a `grounding_status` computed by the extractor's
own anchor-in-section search. This module just emits a deterministic summary of
that precomputed status (matched/ambiguous/not_found counts) as a grounding
leaf, the same way the summarize critic emits its literal value-in-section
check — so a run's tree always shows *some* grounding leaf under "critic" for
either subagent.

It runs inside the `critic` graph node, so its LLM call and that grounding leaf
nest under that node in the dev UI's run tree. The critic uses the faster
``cheap`` role: it only classifies and checks already-grounded candidates, while
the stronger ``reasoner`` remains responsible for proposing the gaps.
"""
from __future__ import annotations

import json

from src.common.utils.issue_tracking import merge_items
from src.common.utils.prompts import load_prompt
from src.common.utils.logging.trace import emit as emit_trace
from src.llm.registry import chat_with_structured_output, get_llm
from src.common.schemas.gap_verify import GapCritique
from ...tools.sections import DEFAULT_SECTION_CHAR_LIMIT, truncate

CRITIC_SYSTEM_PROMPT = load_prompt(__file__)

_ISSUE_FIELDS = ["gap_id", "severity", "reason"]
_HINT_FIELDS = ["hint"]


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
        text = truncate(text, DEFAULT_SECTION_CHAR_LIMIT)
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


def run_critic(sections: dict[str, str], inline_section_names: list[str],
               candidates: list[dict], persisted_issues: list[dict] | None = None,
               persisted_hints: list[dict] | None = None, round_num: int = 1) -> dict:
    """`candidates`: gap_id/title/description/type/canonical_description/
    evidence_section/anchor/quote/grounding_status dicts for the *current*
    accepted set (not raw LLM output) — the critic judges what would
    actually be written, not transient proposals.

    `persisted_issues`/`persisted_hints` (optional): the full, ever-growing
    lists carried by the caller (the gap subgraph) — including items already
    `resolved`, possibly from an earlier top-level `extract_gaps()` call
    entirely. Only each list's still-open items are shown to the LLM for it to
    confirm or drop. A `major` issue's gap gets dropped from `candidates` by
    the caller before its next round, at which point it's no longer eligible to
    be re-confirmed and the merge below resolves it automatically; a `minor`
    issue stays open until the LLM stops re-flagging it (e.g. once the
    extractor has revised the gap's wording).

    Returns `{"status", "issues", "missing_hints"}` where both lists are the
    full updated persisted lists.
    """
    persisted_issues = [dict(i) for i in (persisted_issues or [])]
    persisted_hints = [dict(h) for h in (persisted_hints or [])]
    live_gap_ids = {c["gap_id"] for c in candidates}
    # An issue whose gap was dropped (rejected) since it was flagged has
    # nothing left to re-confirm against — resolve it now rather than
    # leaving it open forever because it can never appear in `candidates`
    # again.
    for issue in persisted_issues:
        if not issue["resolved"] and issue["gap_id"] not in live_gap_ids:
            issue["resolved"] = True
            issue["resolved_round"] = round_num
    open_issues = [i for i in persisted_issues if not i["resolved"]]
    open_hints = [h for h in persisted_hints if not h["resolved"]]

    statuses = [c.get("grounding_status") for c in candidates]
    emit_trace({"type": "tool", "name": "grounding", "data": {
        "n_matched": statuses.count("matched"),
        "n_ambiguous": statuses.count("ambiguous"),
        "n_not_found": statuses.count("not_found"),
        "candidates": [
            {"gap_id": c.get("gap_id"),
             "canonical_description": c.get("canonical_description"),
             "section": c.get("evidence_section"),
             "anchor": c.get("anchor"),
             "grounding_status": c.get("grounding_status"),
             "quote": c.get("quote")}
            for c in candidates
        ],
    }})

    if not candidates:
        return {"status": "pass", "issues": persisted_issues,
                "missing_hints": persisted_hints}

    user_prompt = build_critic_prompt(sections, inline_section_names, candidates,
                                      open_issues=open_issues, open_hints=open_hints)
    raw = chat_with_structured_output(
        get_llm("cheap"), CRITIC_SYSTEM_PROMPT, user_prompt, GapCritique
    ).model_dump()
    raw_issues = raw.get("issues") or []
    valid_issues = [issue for issue in raw_issues if issue.get("gap_id") in live_gap_ids]
    if len(valid_issues) != len(raw_issues):
        emit_trace({"type": "event", "name": "ignored malformed gap critique issues",
                    "data": {"ignored_count": len(raw_issues) - len(valid_issues)}})
    issues = merge_items(persisted_issues, valid_issues, round_num,
                         _ISSUE_FIELDS)
    hints = merge_items(persisted_hints, raw.get("missing_hints") or [], round_num,
                        _HINT_FIELDS)
    n_open_issues = len([i for i in issues if not i["resolved"]])
    n_open_hints = len([h for h in hints if not h["resolved"]])
    return {
        "status": "issues" if (n_open_issues or n_open_hints) else "pass",
        "issues": issues,
        "missing_hints": hints,
    }
