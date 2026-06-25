"""gap subagent — critic (Stage 4b): one stateless critique pass.

Reviews gap candidates an extractor pass already produced: is each one
genuinely stated (not hallucinated/inferred), does its quote actually
support its description, and does anything clearly stated in the given
sections look missing. Only ever returns a verdict over candidates handed
to it — never invents or rewrites a gap, never merges across passes. That
bookkeeping (drop a flagged gap, decide convergence) is `gap.extract_gaps`'s
job.

Each candidate already carries a `grounding_status` computed by
`extractor.run_extract`'s own "grounding" step (the literal anchor-in-section
search that produces the quote) — that's where the actual text search runs,
since the quote has to exist before a candidate can be merged into the
loop's accepted set. This module just logs a deterministic summary of that
precomputed status (matched/ambiguous/not_found counts) under its own
"grounding" step, the same way `summarize.critic` logs its literal
value-in-section check — so a run's log always shows *some* grounding step
nested under "critic" for either subagent, not just under "extractor" here.
"""
from __future__ import annotations

import sys
from pathlib import Path

_NOVADRAFT_ROOT = Path(__file__).resolve().parents[5]
if str(_NOVADRAFT_ROOT) not in sys.path:
    sys.path.insert(0, str(_NOVADRAFT_ROOT))

from src.llm.registry import chat_json  # noqa: E402
from .....common.schemas.gap_verify import GapCritique  # noqa: E402
from .....common.utils.issue_tracking import merge_items  # noqa: E402
from .....common.utils.runlog import step  # noqa: E402
from .critic_prompt import CRITIC_SYSTEM_PROMPT, build_critic_prompt  # noqa: E402

_ISSUE_FIELDS = ["gap_id", "severity", "reason"]
_HINT_FIELDS = ["hint"]


def run_critic(sections: dict[str, str], inline_section_names: list[str],
               candidates: list[dict], persisted_issues: list[dict] | None = None,
               persisted_hints: list[dict] | None = None, round_num: int = 1) -> dict:
    """`candidates`: gap_id/title/description/type/canonical_description/
    evidence_section/anchor/quote/grounding_status dicts for the *current*
    accepted set (not raw LLM output) — the critic judges what would
    actually be written, not transient proposals.

    `persisted_issues`/`persisted_hints` (optional): the full, ever-growing
    lists carried by the caller (`gap.extract_gaps()`) — including items
    already `resolved`, possibly from an earlier top-level `extract_gaps()`
    call entirely. Only each list's still-open items are shown to the LLM
    for it to confirm or drop. A `major` issue's gap gets dropped from
    `candidates` by the caller before its next round, at which point it's no
    longer eligible to be re-confirmed and the merge below resolves it
    automatically; a `minor` issue stays open until the LLM stops
    re-flagging it (e.g. once the extractor has revised the gap's wording).

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

    with step("critic", n_candidates=len(candidates), n_open_issues=len(open_issues),
             n_open_hints=len(open_hints)) as node:
        with step("grounding") as gnode:
            statuses = [c.get("grounding_status") for c in candidates]
            gnode["data"]["result"] = {
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
            }

        if not candidates:
            result = {"status": "pass", "issues": persisted_issues,
                      "missing_hints": persisted_hints}
        else:
            user_prompt = build_critic_prompt(sections, inline_section_names, candidates,
                                              open_issues=open_issues, open_hints=open_hints)
            raw = chat_json(CRITIC_SYSTEM_PROMPT, user_prompt, response_schema=GapCritique)
            issues = merge_items(persisted_issues, raw.get("issues") or [], round_num,
                                 _ISSUE_FIELDS)
            hints = merge_items(persisted_hints, raw.get("missing_hints") or [], round_num,
                                _HINT_FIELDS)
            n_open_issues = len([i for i in issues if not i["resolved"]])
            n_open_hints = len([h for h in hints if not h["resolved"]])
            result = {
                "status": "issues" if (n_open_issues or n_open_hints) else "pass",
                "issues": issues,
                "missing_hints": hints,
            }
        node["data"]["result"] = {"status": result.get("status"),
                                   "issues": result["issues"],
                                   "missing_hints": result["missing_hints"]}
        return result
