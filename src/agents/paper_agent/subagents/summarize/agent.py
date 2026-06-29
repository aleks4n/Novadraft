"""summarize subagent — the LangGraph subgraph (Stage 3 + 3b).

Wires the extractor and critic subagents into a bounded cycle:

    enter → extractor → critic → (route: regenerate? → extractor : → finalize) → END

`extractor.run_extract` (one LLM pass) and `critic.run_critic` (one grounding
check + one LLM pass) are both stateless single-pass functions. This module
owns everything between them — the regenerate/critique loop, deciding when a
critique counts as "major enough to retry", shaping that critique into
feedback for the next extractor pass, and writing the final
`summary_verification` record once the loop ends.

The loop is bounded by `MAX_SUMMARY_LOOPS` rounds (enforced in the router) and
stops early once a round's critique passes clean or plateaus. This is the same
logic the old imperative `while` loop ran; it is now expressed as a graph cycle
so each round's extractor/critic step (and its LLM call) streams into the dev
UI's run tree.

`summarize(category_slug, paper_id, force=False)` compiles and invokes the
graph and returns the same result dict as before — it stays importable and
standalone-callable (used by the dev harness and the eval pipeline).
"""
from __future__ import annotations

import json

from langgraph.graph import END, START, StateGraph

from src.common.utils import storage
from src.common.utils.trace import emit as emit_trace
from .state import SummarizeState
from .subagents.critic.agent import run_critic
from .subagents.extractor.agent import PaperNotFoundError, run_extract

MAX_SUMMARY_LOOPS = 10

# enter + MAX_SUMMARY_LOOPS*(extractor+critic) + finalize, with headroom.
_RECURSION_LIMIT = 2 * MAX_SUMMARY_LOOPS + 10


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
    into the shape `run_extract` expects for its next regenerate call.
    Resolved issues are never surfaced to the extractor — they've already
    been fixed."""
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
        raise PaperNotFoundError(f"{category_slug}/{paper_id}")

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


# --- graph nodes -------------------------------------------------------------

def enter(state: SummarizeState) -> dict:
    """Read the paper, short-circuit if it's already verified, otherwise set
    up the loop carry from any prior verification record."""
    category_slug, paper_id = state["category_slug"], state["paper_id"]
    force = state.get("force", False)

    record = storage.read_paper_json(category_slug, paper_id)
    processing = (record or {}).get("processing") or {}

    if processing.get("summary_verified") and not force:
        emit_trace({"type": "event", "name": "already verified, skipping",
                    "data": {"paper_id": paper_id}})
        extract_result = run_extract(category_slug, paper_id, force=force)
        result = {
            **extract_result,
            "verification": {"status": "skipped", "reason": "already_verified"},
        }
        return {"skip": True, "summary_result": result}

    prior_verification = (record or {}).get("summary_verification") or {}
    persisted_issues = [dict(i) for i in prior_verification.get("issues") or []]
    persisted_hints = [dict(h) for h in prior_verification.get("missing_hints") or []]
    open_issues_at_start = [i for i in persisted_issues if not i["resolved"]]
    open_hint_texts = [h["hint"] for h in persisted_hints if not h["resolved"]]
    feedback = _build_feedback(open_issues_at_start, []) if open_issues_at_start else None

    return {
        "skip": False,
        "done": False,
        "attempt": 0,
        "regenerated": False,
        "critique": None,
        "prev_signature": None,
        "persisted_issues": persisted_issues,
        "persisted_hints": persisted_hints,
        "open_hint_texts": open_hint_texts,
        "feedback": feedback,
    }


def extractor(state: SummarizeState) -> dict:
    """One extractor pass (loop body, part 1). Increments the attempt counter,
    re-summarizes with the current feedback/missing-hints, and breaks early if
    the very first pass produced no summary at all to critique."""
    category_slug, paper_id = state["category_slug"], state["paper_id"]
    force = state.get("force", False)
    attempt = state["attempt"] + 1
    feedback = state.get("feedback")
    open_hint_texts = state.get("open_hint_texts") or []

    extract_result = run_extract(
        category_slug, paper_id,
        force=force or attempt > 1 or bool(feedback) or bool(open_hint_texts),
        feedback=feedback,
        missing_hints=open_hint_texts or None,
    )
    regenerated = (state.get("regenerated", False) or attempt > 1 or bool(feedback)
                   or bool(open_hint_texts))
    updates = {"attempt": attempt, "summarize_extract": extract_result,
               "regenerated": regenerated}

    if extract_result.get("status") == "error" and attempt == 1:
        # Nothing to critique yet — the very first extraction never produced
        # a summary at all (e.g. no sections, or an empty LLM response with no
        # prior summary to fall back to). Any later round failing the same way
        # still leaves the earlier successful summary in place, so the critic
        # can — and should — still judge it; only this first-round case has
        # truly nothing to hand the critic.
        emit_trace({"type": "event",
                    "name": "extractor failed before any summary existed, stopping",
                    "data": {"attempt": attempt, "reason": extract_result.get("reason")}})
        updates["critique"] = None
        updates["done"] = True
        return updates

    if extract_result.get("status") == "error":
        emit_trace({"type": "event",
                    "name": "extractor failed this round, re-critiquing the existing "
                            "summary unchanged",
                    "data": {"attempt": attempt, "reason": extract_result.get("reason")}})
    updates["done"] = False
    return updates


def critic(state: SummarizeState) -> dict:
    """One critique pass (loop body, part 2). Re-critiques the current summary,
    merges the verdict into the persisted issue/hint lists, and decides whether
    another regenerate round is warranted (and, if so, shapes the feedback)."""
    category_slug, paper_id = state["category_slug"], state["paper_id"]
    attempt = state["attempt"]

    critique = run_critic(category_slug, paper_id,
                          persisted_issues=state["persisted_issues"],
                          persisted_hints=state["persisted_hints"],
                          round_num=attempt)
    persisted_issues = critique["issues"]
    persisted_hints = critique["missing_hints"]
    updates = {"critique": critique, "persisted_issues": persisted_issues,
               "persisted_hints": persisted_hints}

    if not _needs_another_round(critique):
        updates["done"] = True
        return updates

    signature = _unresolved_signature(critique)
    if signature == state.get("prev_signature"):
        unmatched, open_major_ids, open_hint_ids = signature
        emit_trace({"type": "event", "name": "no progress since last round, stopping",
                    "data": {"attempt": attempt, "n_unmatched": len(unmatched),
                             "n_open_major_issues": len(open_major_ids),
                             "n_open_hints": len(open_hint_ids)}})
        updates["done"] = True
        return updates

    open_hint_texts = [h["hint"] for h in persisted_hints if not h["resolved"]]
    n_open_issues = len([i for i in persisted_issues if not i["resolved"]])
    emit_trace({"type": "event", "name": "major issue or missing coverage found, "
                                         "will regenerate",
                "data": {"attempt": attempt, "n_open_issues": n_open_issues,
                         "n_open_hints": len(open_hint_texts)}})
    updates["prev_signature"] = signature
    updates["open_hint_texts"] = open_hint_texts
    updates["feedback"] = _build_feedback(persisted_issues, critique["grounding"]["unmatched"])
    updates["done"] = False
    return updates


def finalize(state: SummarizeState) -> dict:
    """Write the summary_verification record and assemble the final result."""
    verify_result = _write_verification(
        state["category_slug"], state["paper_id"], state.get("critique"),
        state["attempt"], state.get("regenerated", False),
    )
    result = {**state["summarize_extract"], "verification": verify_result}
    return {"summary_result": result}


# --- routers -----------------------------------------------------------------

def _route_after_enter(state: SummarizeState) -> str:
    return END if state.get("skip") else "extractor"


def _route_after_extractor(state: SummarizeState) -> str:
    return "finalize" if state.get("done") else "critic"


def _route_after_critic(state: SummarizeState) -> str:
    if state.get("done") or state["attempt"] >= state.get("max_loops", MAX_SUMMARY_LOOPS):
        return "finalize"
    return "extractor"


def build_summarize_graph():
    """Compile the summarize subgraph (extractor↔critic cycle)."""
    g = StateGraph(SummarizeState)
    g.add_node("enter", enter)
    g.add_node("extractor", extractor)
    g.add_node("critic", critic)
    g.add_node("finalize", finalize)

    g.add_edge(START, "enter")
    g.add_conditional_edges("enter", _route_after_enter, {"extractor": "extractor", END: END})
    g.add_conditional_edges("extractor", _route_after_extractor,
                            {"critic": "critic", "finalize": "finalize"})
    g.add_conditional_edges("critic", _route_after_critic,
                            {"extractor": "extractor", "finalize": "finalize"})
    g.add_edge("finalize", END)
    return g.compile()


_GRAPH = None


def summarize(category_slug: str, paper_id: str, force: bool = False,
              max_loops: int = MAX_SUMMARY_LOOPS) -> dict:
    """Run the summarize subgraph for one paper, returning the same result
    dict the old `summarize()` did. Standalone-callable (the dev harness and
    eval pipeline import it directly)."""
    global _GRAPH
    if _GRAPH is None:
        _GRAPH = build_summarize_graph()
    final = _GRAPH.invoke(
        {"category_slug": category_slug, "paper_id": paper_id, "force": force,
         "max_loops": max_loops},
        config={"recursion_limit": _RECURSION_LIMIT},
    )
    return final["summary_result"]
