"""gap subagent (Stage 4 + 4b) — the only entry point this package exposes.

`extractor.run_extract` (one LLM pass + deterministic anchor-grounding) and
`critic.run_critic` (one LLM pass) are both stateless single-pass functions
with no disk I/O. This module owns everything around them: reading the
paper once before the loop starts, the bounded extract/critique loop
itself (merging candidates across passes, deciding what counts as
converged), and writing the final accepted set to disk once the loop ends.

Bounded by `MAX_GAP_LOOPS` rounds; stops early once there's no outstanding
cleanup work (nothing rejected this round, no anchor still needs fixing) and
either the critic confirms coverage is complete or the extractor stops
finding anything new — whichever comes first, so a critic that's satisfied
on round 1 doesn't force a wasted confirmation round. A minor issue
re-flagged `MAX_MINOR_RECONFIRMS` times in a row (extractor and critic
oscillating on a debatable `type`/wording call, never actually fixing it) is
auto-resolved rather than counted as outstanding work, so that alone can't
exhaust the loop budget. See PAPER_AND_GAP_AGENT.md, Stage 4 + 4b.

Out of scope (handled later by GapCheckAgent):
- Cross-paper dedup
- Online "is this already solved?" check
- Severity / novelty judgment
- Promotion from pending/ to verified gaps
- Resolving ambiguous-anchor gaps (left for a future GapEvaluator stage)
"""
from __future__ import annotations

import json
from pathlib import Path

from .....common.utils import storage
from .....common.utils.runlog import log_event, step
from .....common.schemas.gap import GapEvidence, GapRecord

from . import critic, extractor
from .extractor_prompt import select_gap_sections

MAX_GAP_LOOPS = 10

# A minor issue (e.g. a debatable `type`) re-flagged this many times despite
# revision attempts is treated as unresolvable nitpicking rather than a real
# problem — see `_run_critique_round`.
MAX_MINOR_RECONFIRMS = 2


class PaperNotFoundError(Exception):
    pass


class NotSummarizedError(Exception):
    pass


def _pending_dir(category_slug: str) -> Path:
    d = storage.category_dir(category_slug) / "gaps" / "pending"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _read_category_meta(category_slug: str) -> dict | None:
    path = storage.category_dir(category_slug) / "meta.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _write_paper(category_slug: str, record: dict) -> None:
    pdir = storage.paper_dir(category_slug, record["paper_id"])
    (pdir / "paper.json").write_text(json.dumps(record, indent=2, ensure_ascii=False))


def _prepare_context(category_slug: str, paper_id: str, force: bool) -> dict:
    """One-time setup before the loop starts. Returns either
    `{"skip": <result dict>}` (paper already has gaps and `force` is False)
    or the static context every round needs."""
    record = storage.read_paper_json(category_slug, paper_id)
    if record is None:
        raise PaperNotFoundError(f"{category_slug}/{paper_id}")

    processing = record.get("processing") or {}
    if not processing.get("summarized"):
        raise NotSummarizedError(
            f"{paper_id}: run summarize first (processing.summarized is false)"
        )
    if processing.get("gaps_extracted") and not force:
        existing = ((record.get("gaps") or {}).get("gap_ids")) or []
        log_event("already extracted, skipping", paper_id=paper_id)
        return {"skip": {
            "status": "skipped", "paper_id": paper_id,
            "reason": "already_extracted", "gap_ids": existing,
        }}

    sections = ((record.get("content") or {}).get("sections")) or {}
    return {
        "paper_id": paper_id,
        "sections": sections,
        "summary": record.get("summary") or {},
        "metadata": record.get("metadata") or {},
        "inline_sections": select_gap_sections(list(sections.keys())),
        "category_meta": _read_category_meta(category_slug),
    }


_ANCHOR_FIELDS = {"anchor", "quote", "char_offset", "grounding_status",
                  "ambiguous_offsets", "record_status", "gap_id"}
_CONTENT_FIELDS = {"title", "description", "type"}


def _open_minor_issues_by_gap(state: dict) -> dict[str, list[dict]]:
    by_gap: dict[str, list[dict]] = {}
    for issue in state["issues"]:
        if issue["resolved"] or issue["severity"] != "minor":
            continue
        by_gap.setdefault(issue["gap_id"], []).append(issue)
    return by_gap


def _run_extract_round(ctx: dict, state: dict) -> None:
    """One extractor round: builds loop context from `state`, calls
    `extractor.run_extract`, and merges the results into `state["accepted"]`
    (same-gid candidates only replace the existing one if their grounding
    wasn't already "matched" — a confirmed-good gap is never overwritten by
    a later, possibly worse, re-proposal). Mutates `state`.

    For a gid that was a retry-anchor case going into this round, the merge
    is field-scoped: title/description/type/canonical_description/section
    are kept from the existing accepted record, not the re-proposal — the
    prompt already asks the model to echo those back unchanged (see
    `extractor_prompt._format_retry_anchor`), but trusting it to do so
    verbatim is exactly the kind of silent-drift risk that motivated this
    change. Only the anchor/grounding-derived fields actually come from the
    new candidate.

    For a gid with an open `minor` critic issue (`needs_revision`), the merge
    runs the other way: the candidate is expected to revise title/description
    /type to address the issue while keeping canonical_description/section
    (and therefore its anchor) the same, so anchor-derived fields are kept
    from the existing record and only the content fields come from the new
    candidate.
    """
    retry_anchor_gids = set(state["retry_anchor"].keys())
    needs_revision = _open_minor_issues_by_gap(state)
    needs_revision_gids = {gid for gid in needs_revision if gid in state["accepted"]}
    loop_context = {
        "already_found": [
            {"canonical_description": r["canonical_description"]}
            for gid, r in state["accepted"].items()
            if gid not in state["retry_anchor"] and gid not in needs_revision_gids
        ],
        "rejected": [{"canonical_description": c, "reason": r}
                     for c, r in state["rejected"].items()],
        "retry_anchor": [
            {**state["accepted"][gid], "reason": reason}
            for gid, reason in state["retry_anchor"].items() if gid in state["accepted"]
        ],
        "needs_revision": [
            {**state["accepted"][gid],
             "reason": "; ".join(i["reason"] for i in issues)}
            for gid, issues in needs_revision.items() if gid in state["accepted"]
        ],
        "missing_hints": [h["hint"] for h in state["missing_hints"] if not h["resolved"]],
    }
    pass_result = extractor.run_extract(ctx, loop_context)
    state["warnings"].extend(pass_result["warnings"])

    new_or_fixed = 0
    for cand in pass_result["candidates"]:
        gid = cand["gap_id"]
        if cand["canonical_description"] in state["rejected"] and gid not in state["accepted"]:
            # The extractor re-proposed something already rejected, despite
            # being told not to (it's only told via the prompt, which it
            # doesn't always follow) — drop it rather than re-litigating.
            continue
        existing = state["accepted"].get(gid)
        if existing is not None and existing["grounding_status"] == "matched" \
                and gid not in needs_revision_gids:
            continue  # true duplicate, nothing new to learn

        if gid in needs_revision_gids and existing is not None:
            merged = dict(existing)
            merged.update({k: v for k, v in cand.items() if k in _CONTENT_FIELDS})
            state["accepted"][gid] = merged
            new_or_fixed += 1
            continue

        if cand["grounding_status"] == "matched":
            state["retry_anchor"].pop(gid, None)
        elif cand["grounding_status"] == "ambiguous":
            state["retry_anchor"][gid] = (
                f"anchor matched {len(cand['ambiguous_offsets'])} spots in '{cand['section']}'"
            )
        else:
            state["retry_anchor"][gid] = f"anchor not found verbatim in '{cand['section']}'"

        if gid in retry_anchor_gids and existing is not None:
            merged = dict(existing)
            merged.update({k: v for k, v in cand.items() if k in _ANCHOR_FIELDS})
            cand = merged

        state["accepted"][gid] = cand
        new_or_fixed += 1

    state["_raw_count"] = pass_result["raw_count"]
    state["_new_or_fixed"] = new_or_fixed


def _run_critique_round(ctx: dict, state: dict) -> None:
    """One critique round: calls `critic.run_critic` over `state["accepted"]`,
    drops any gap a still-open `major` issue flags (moving it to
    `state["rejected"]` so the extractor won't re-propose it), records
    missing-coverage hints for the next round, and sets `state["converged"]`.
    Mutates `state`.
    """
    candidates = [
        {"gap_id": gid, "title": r["title"], "description": r["description"],
         "type": r["type"], "canonical_description": r["canonical_description"],
         "evidence_section": r["section"], "anchor": r["anchor"],
         "quote": r["quote"], "grounding_status": r["grounding_status"]}
        for gid, r in state["accepted"].items()
    ]
    round_num = len(state["loop_trail"]) + 1
    critique = critic.run_critic(ctx["sections"], ctx["inline_sections"], candidates,
                                 persisted_issues=state["issues"],
                                 persisted_hints=state["missing_hints"],
                                 round_num=round_num)
    state["issues"] = critique["issues"]
    state["missing_hints"] = critique["missing_hints"]

    major_issues = [i for i in state["issues"]
                    if not i["resolved"] and i["severity"] == "major"]
    for issue in major_issues:
        gid = issue["gap_id"]
        rec = state["accepted"].pop(gid, None)
        if rec is not None:
            state["retry_anchor"].pop(gid, None)
            state["rejected"][rec["canonical_description"]] = issue["reason"]
            state["warnings"].append(
                f"gap_rejected_by_critic: gid={gid} reason={issue['reason']}"
            )
            issue["resolved"] = True
            issue["resolved_round"] = round_num

    # A minor issue (typically a debatable `type`/wording call) that's still
    # being re-flagged after several revision attempts isn't converging —
    # the extractor and critic are just oscillating between equally-arguable
    # labels. Stop asking for another revision and accept the gap as-is
    # rather than burning the rest of the loop budget on it.
    for issue in state["issues"]:
        if not issue["resolved"] and issue["severity"] == "minor" \
                and issue.get("reconfirm_count", 0) >= MAX_MINOR_RECONFIRMS:
            issue["resolved"] = True
            issue["resolved_round"] = round_num
            state["warnings"].append(
                f"gap_minor_issue_given_up: gid={issue['gap_id']} "
                f"reason={issue['reason']}"
            )

    coverage_complete = not any(not h["resolved"] for h in state["missing_hints"])
    open_minor = [i for i in state["issues"] if not i["resolved"] and i["severity"] == "minor"
                 and i["gap_id"] in state["accepted"]]
    state["loop_trail"].append({
        "attempt": len(state["loop_trail"]) + 1,
        "n_candidates": state.pop("_raw_count", 0),
        "n_new_or_fixed": state.pop("_new_or_fixed", 0),
        "n_major_issues": len(major_issues),
        "n_open_minor_issues": len(open_minor),
        "coverage_complete": coverage_complete,
    })
    last = state["loop_trail"][-1]
    # Converge once there's no outstanding cleanup work (nothing this round
    # got rejected, nothing still needs a better anchor, no open minor issue
    # still waiting on a revision) AND either the critic confirms coverage is
    # complete, OR the extractor plateaued (found nothing new despite being
    # asked) — whichever comes first. Requiring *both* coverage_complete and
    # new_or_fixed==0 would force a wasted extra round every time just to
    # "confirm" a round that was already fine: the very first round that
    # finds anything always has new_or_fixed > 0, so a critic that's
    # satisfied immediately still couldn't stop the loop.
    no_outstanding_work = not major_issues and not state["retry_anchor"] and not open_minor
    state["converged"] = no_outstanding_work and (coverage_complete or last["n_new_or_fixed"] == 0)


def _finalize(category_slug: str, paper_id: str, state: dict, attempts: int) -> dict:
    """Write the loop's final accepted set to disk."""
    record = storage.read_paper_json(category_slug, paper_id)
    if record is None:
        raise PaperNotFoundError(f"{category_slug}/{paper_id}")

    accepted, rejected, warnings = state["accepted"], state["rejected"], state["warnings"]
    pending_dir = _pending_dir(category_slug)
    gap_ids: list[str] = []
    for gid, r in accepted.items():
        if r["grounding_status"] == "ambiguous":
            warnings.append(
                f"gap_anchor_ambiguous: gid={gid} section={r['section']} "
                f"anchor={r['anchor']!r} matched {len(r['ambiguous_offsets'])} spots "
                f"(left unresolved for a future review/evaluator stage)"
            )
        elif r["grounding_status"] == "not_found":
            warnings.append(
                f"gap_anchor_not_found: gid={gid} section={r['section']} "
                f"anchor={r['anchor']!r} not found verbatim in section text "
                f"after {attempts} pass(es)"
            )

        gap_record = GapRecord(
            gap_id=gid,
            status=r["record_status"],
            title=r["title"],
            description=r["description"],
            type=r["type"],
            canonical_description=r["canonical_description"],
            evidence=[GapEvidence(
                paper_id=paper_id,
                section=r["section"],
                anchor=r["anchor"],
                quote=r["quote"],
                char_offset=r["char_offset"],
                grounding_status=r["grounding_status"],
                ambiguous_offsets=r["ambiguous_offsets"],
            )],
            extracted_at=storage.now_iso(),
        )
        (pending_dir / f"{gid}.json").write_text(gap_record.model_dump_json(indent=2))
        gap_ids.append(gid)

    n_open_issues = len([i for i in state["issues"] if not i["resolved"]])
    n_open_hints = len([h for h in state["missing_hints"] if not h["resolved"]])
    record["gaps"] = {"gap_ids": gap_ids}
    record.setdefault("processing", {})["gaps_extracted"] = True
    record["gap_verification"] = {
        "status": "issues" if (n_open_issues or n_open_hints) else "pass",
        "attempts": attempts,
        "loop_trail": state["loop_trail"],
        "rejected": rejected,
        "issues": state["issues"],
        "missing_hints": state["missing_hints"],
        "checked_at": storage.now_iso(),
    }
    if warnings:
        record.setdefault("warnings", []).extend(warnings)
    _write_paper(category_slug, record)

    return {
        "status": "ok",
        "paper_id": paper_id,
        "n_gaps": len(gap_ids),
        "gap_ids": gap_ids,
        "attempts": attempts,
        "loop_trail": state["loop_trail"],
        "issues": state["issues"],
        "missing_hints": state["missing_hints"],
        "warnings": warnings,
    }


def extract_gaps(category_slug: str, paper_id: str, force: bool = False,
                  max_loops: int = MAX_GAP_LOOPS) -> dict:
    with step("gap", paper_id=paper_id, max_loops=max_loops) as node:
        ctx = _prepare_context(category_slug, paper_id, force)
        if "skip" in ctx:
            node["data"]["result"] = ctx["skip"]
            return ctx["skip"]

        record = storage.read_paper_json(category_slug, paper_id)
        prior_verification = (record or {}).get("gap_verification") or {}
        prior_issues = [dict(i) for i in prior_verification.get("issues") or []]
        prior_hints = [dict(h) for h in prior_verification.get("missing_hints") or []]

        state = {
            "accepted": {}, "rejected": {}, "retry_anchor": {},
            "issues": prior_issues, "missing_hints": prior_hints,
            "warnings": [], "loop_trail": [], "converged": False,
        }
        attempt = 0
        while attempt < max_loops:
            attempt += 1
            _run_extract_round(ctx, state)
            _run_critique_round(ctx, state)
            if state["converged"]:
                break

        result = _finalize(category_slug, paper_id, state, attempt)
        node["data"]["result"] = result
        return result
