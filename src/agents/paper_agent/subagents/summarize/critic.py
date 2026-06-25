"""summarize subagent — critic (Stage 3b): one stateless critique pass.

Runs two checks against the paper's *current* `paper.json::summary`:
  1. Grounding check (deterministic) — do the metric values / dataset names /
     headline numbers actually appear in `content.sections`?
  2. Critic check (single LLM call) — does the summary faithfully and
     completely represent the source sections?

`run_critic()` only judges — it never regenerates the summary and never
writes to paper.json. The loop that decides whether to regenerate (call
`extractor.run_extract` again with feedback, then re-critique, bounded by a
round limit) lives in `summarize.summarize()`.
"""
from __future__ import annotations

import sys
from pathlib import Path

from .....common.utils import storage
from .....common.utils.issue_tracking import merge_items
from .....common.utils.runlog import step

_NOVADRAFT_ROOT = Path(__file__).resolve().parents[5]
if str(_NOVADRAFT_ROOT) not in sys.path:
    sys.path.insert(0, str(_NOVADRAFT_ROOT))

from src.llm.registry import chat_json  # noqa: E402
from .....common.schemas.summary_verify import SummaryCritique  # noqa: E402
from .critic_prompt import SYSTEM_PROMPT, build_critic_prompt  # noqa: E402
from .grounding import check_grounding  # noqa: E402

_ISSUE_FIELDS = ["field", "severity", "description", "evidence_section"]
_HINT_FIELDS = ["hint"]


class PaperNotFoundError(Exception):
    pass


class NotSummarizedError(Exception):
    pass


def run_critic(category_slug: str, paper_id: str, persisted_issues: list[dict] | None = None,
              persisted_hints: list[dict] | None = None, round_num: int = 1) -> dict:
    """Judge the paper's current summary. Reads fresh from paper.json each
    call, so a caller looping extractor -> critic always critiques the
    latest summary, not a stale copy.

    `persisted_issues`/`persisted_hints` (optional): the full, ever-growing
    lists carried by the caller (`summarize.summarize()`) — including items
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
    open_issues = [i for i in persisted_issues if not i["resolved"]]
    open_hints = [h for h in persisted_hints if not h["resolved"]]

    with step("critic", paper_id=paper_id, n_open_issues=len(open_issues),
             n_open_hints=len(open_hints)) as node:
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

        with step("grounding") as gnode:
            grounding = check_grounding(summary, sections)
            gnode["data"]["result"] = {"checked": grounding["checked"],
                                       "unmatched": grounding["unmatched"]}
        user_prompt = build_critic_prompt(sections, summary, open_issues=open_issues,
                                          open_hints=open_hints)
        critique = chat_json(SYSTEM_PROMPT, user_prompt, response_schema=SummaryCritique)

        issues = merge_items(persisted_issues, critique.get("issues") or [], round_num,
                             _ISSUE_FIELDS)
        hints = merge_items(persisted_hints, critique.get("missing_hints") or [], round_num,
                            _HINT_FIELDS)
        n_open_issues = len([i for i in issues if not i["resolved"]])
        n_open_hints = len([h for h in hints if not h["resolved"]])

        result = {
            "status": "issues" if (grounding["unmatched"] or n_open_issues or n_open_hints)
                      else "pass",
            "grounding": grounding,
            "issues": issues,
            "missing_hints": hints,
        }
        node["data"]["result"] = {"status": result["status"],
                                   "n_unmatched": len(grounding["unmatched"]),
                                   "issues": issues, "missing_hints": hints}
        return result
