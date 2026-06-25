"""summarize subagent (Stage 3 + 3b) — the only entry point this package
exposes.

`extractor.run_extract` (one LLM pass) and `critic.run_critic` (one
grounding check + one LLM pass) are both stateless single-pass functions.
This module owns everything between them: the bounded regenerate/critique
loop, deciding when a critique counts as "major enough to retry", shaping
that critique into feedback for the next extractor pass, and writing the
final `summary_verification` record once the loop ends.

Bounded by `MAX_SUMMARY_LOOPS` rounds; stops early once a round's critique
passes clean. See PAPER_AND_GAP_AGENT.md, Stage 3 + 3b.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from .....common.utils import storage
from .....common.utils.runlog import log_event, step

_NOVADRAFT_ROOT = Path(__file__).resolve().parents[5]
if str(_NOVADRAFT_ROOT) not in sys.path:
    sys.path.insert(0, str(_NOVADRAFT_ROOT))

from . import critic, extractor  # noqa: E402

MAX_SUMMARY_LOOPS = 10


def _needs_another_round(critique: dict) -> bool:
    """A major issue / unmatched grounding value is "something's wrong"; an
    open missing-hint is "something's missing entirely" — either one is
    reason enough to regenerate."""
    open_major = any(i["severity"] == "major" for i in critique["issues"] if not i["resolved"])
    open_hints = any(not h["resolved"] for h in critique["missing_hints"])
    return bool(critique["grounding"]["unmatched"]) or open_major or open_hints


def _unresolved_signature(critique: dict) -> tuple:
    """Snapshot of everything still outstanding after this round: unmatched
    grounding values, open major-issue ids, open missing-hint ids. Used to
    detect a plateau — if this is identical to the previous round's
    signature, the extractor's last regenerate pass changed nothing, so
    another attempt won't either."""
    open_major_ids = sorted(
        i["id"] for i in critique["issues"] if not i["resolved"] and i["severity"] == "major"
    )
    open_hint_ids = sorted(h["id"] for h in critique["missing_hints"] if not h["resolved"])
    return (tuple(sorted(critique["grounding"]["unmatched"])), tuple(open_major_ids),
            tuple(open_hint_ids))


def _build_feedback(issues: list[dict], unmatched: list[str]) -> list[dict]:
    """Flatten the still-open issues (plus this round's grounding misses)
    into the shape `extractor.run_extract` expects for its next regenerate
    call. Resolved issues are never surfaced to the extractor — they've
    already been fixed."""
    feedback = [
        {
            "field": "metrics/datasets/results.headline",
            "severity": "major",
            "description": f"Value '{value}' does not appear in the paper's extracted sections.",
            "evidence_section": None,
        }
        for value in unmatched
    ]
    feedback.extend(i for i in issues if not i["resolved"])
    return feedback


def _write_verification(category_slug: str, paper_id: str, critique: dict | None,
                        attempts: int, regenerated: bool) -> dict:
    record = storage.read_paper_json(category_slug, paper_id)
    if record is None:
        raise extractor.PaperNotFoundError(f"{category_slug}/{paper_id}")

    issues = critique["issues"] if critique else []
    hints = critique["missing_hints"] if critique else []
    verification = {
        "status": critique["status"] if critique else "error",
        "attempts": attempts,
        "regenerated": regenerated,
        "grounding": critique["grounding"] if critique else {"checked": [], "unmatched": []},
        "issues": issues,
        "missing_hints": hints,
        "checked_at": storage.now_iso(),
    }
    record["summary_verification"] = verification
    record.setdefault("processing", {})["summary_verified"] = critique is not None
    pdir = storage.paper_dir(category_slug, paper_id)
    (pdir / "paper.json").write_text(json.dumps(record, indent=2, ensure_ascii=False))

    n_open_issues = len([i for i in issues if not i["resolved"]])
    n_open_hints = len([h for h in hints if not h["resolved"]])
    return {
        "status": "ok" if critique else "error",
        "paper_id": paper_id,
        "verification_status": verification["status"],
        "attempts": attempts,
        "regenerated": regenerated,
        "issues": issues,
        "missing_hints": hints,
        "n_open_issues": len(verification["grounding"]["unmatched"]) + n_open_issues,
        "n_open_hints": n_open_hints,
    }


def summarize(category_slug: str, paper_id: str, force: bool = False,
              max_loops: int = MAX_SUMMARY_LOOPS) -> dict:
    with step("summarize", paper_id=paper_id, max_loops=max_loops) as node:
        record = storage.read_paper_json(category_slug, paper_id)
        processing = (record or {}).get("processing") or {}

        if processing.get("summary_verified") and not force:
            log_event("already verified, skipping", paper_id=paper_id)
            extract_result = extractor.run_extract(category_slug, paper_id, force=force)
            result = {
                **extract_result,
                "verification": {"status": "skipped", "reason": "already_verified"},
            }
            node["data"]["result"] = result
            return result

        prior_verification = (record or {}).get("summary_verification") or {}
        persisted_issues = [dict(i) for i in prior_verification.get("issues") or []]
        persisted_hints = [dict(h) for h in prior_verification.get("missing_hints") or []]
        open_issues_at_start = [i for i in persisted_issues if not i["resolved"]]
        open_hint_texts = [h["hint"] for h in persisted_hints if not h["resolved"]]
        feedback = _build_feedback(open_issues_at_start, []) if open_issues_at_start else None

        critique, regenerated = None, False
        attempt = 0
        prev_signature = None
        while attempt < max_loops:
            attempt += 1
            extract_result = extractor.run_extract(
                category_slug, paper_id,
                force=force or attempt > 1 or bool(feedback) or bool(open_hint_texts),
                feedback=feedback,
                missing_hints=open_hint_texts or None,
            )
            regenerated = (regenerated or attempt > 1 or bool(feedback)
                          or bool(open_hint_texts))
            if extract_result.get("status") == "error" and attempt == 1:
                # Nothing to critique yet — the very first extraction never
                # produced a summary at all (e.g. no sections, or an empty
                # LLM response with no prior summary to fall back to). Any
                # later round failing the same way still leaves the earlier
                # successful summary in place, so the critic can — and
                # should — still judge it; only this first-round case has
                # truly nothing to hand the critic.
                critique = None
                log_event("extractor failed before any summary existed, stopping",
                          attempt=attempt, reason=extract_result.get("reason"))
                break
            if extract_result.get("status") == "error":
                log_event("extractor failed this round, re-critiquing the existing summary "
                          "unchanged", attempt=attempt, reason=extract_result.get("reason"))
            critique = critic.run_critic(category_slug, paper_id,
                                         persisted_issues=persisted_issues,
                                         persisted_hints=persisted_hints,
                                         round_num=attempt)
            persisted_issues = critique["issues"]
            persisted_hints = critique["missing_hints"]
            if not _needs_another_round(critique):
                break
            signature = _unresolved_signature(critique)
            if signature == prev_signature:
                unmatched, open_major_ids, open_hint_ids = signature
                log_event("no progress since last round, stopping", attempt=attempt,
                          n_unmatched=len(unmatched), n_open_major_issues=len(open_major_ids),
                          n_open_hints=len(open_hint_ids))
                break
            prev_signature = signature
            open_hint_texts = [h["hint"] for h in persisted_hints if not h["resolved"]]
            n_open_issues = len([i for i in persisted_issues if not i["resolved"]])
            log_event("major issue or missing coverage found, will regenerate",
                      attempt=attempt, n_open_issues=n_open_issues,
                      n_open_hints=len(open_hint_texts))
            feedback = _build_feedback(persisted_issues, critique["grounding"]["unmatched"])

        verify_result = _write_verification(category_slug, paper_id, critique, attempt, regenerated)
        result = {**extract_result, "verification": verify_result}
        node["data"]["result"] = result
        return result
