"""Reflect (LLM, cheap) — did the plan meet its own criteria, and what now?

A structural check, so it runs on the cheap model. It also drafts the message the
user sees, since it knows both what was asked and what came back — but only on
turns where tools actually ran; a turn that needed none is answered by the planner
and routed straight to finish.

It gets the same context summary the planner got, plus the steps themselves rather
than only their output, so it can distinguish "the tools returned nothing" from
"nothing was selected" and report on the approach as well as the results.
"""

from __future__ import annotations

from src.common.schemas.context import Context
from src.common.utils.prompts import load_prompt
from src.common.utils.logging.trace import log_event
from src.llm.registry import chat_with_structured_output, get_llm
from src.agents.orchestrator_agent.render import context_view, history_view, results_view
from src.agents.orchestrator_agent.schemas import Plan, ReflectJudgment
from src.agents.orchestrator_agent.state import MAX_ITERATIONS, OrchestratorState

_SYSTEM = load_prompt(__file__, "../voice.md") + "\n" + load_prompt(__file__)


def _format_failure(exc: ValueError) -> bool:
    """True only for the registry's exhausted structured-output re-asks."""
    return "model returned no parseable structured output" in str(exc)


def _fallback_judgment(results) -> ReflectJudgment:
    """Finish safely when review formatting fails after the actual work succeeded.

    Reflect is advisory: it decides whether another round is worthwhile and writes the
    short accompanying message. Its inability to serialize that decision must not throw
    away candidates or findings that the expensive tool steps already completed.
    """
    successful = [result for result in results if result.ok]
    failed = [result for result in results if not result.ok]
    candidate_count = sum(len(result.ideas) for result in successful)
    findings = [result.text.strip() for result in successful
                if result.tool == "investigator" and result.text.strip()]

    answer_parts = findings
    if candidate_count:
        noun = "candidate" if candidate_count == 1 else "candidates"
        answer_parts.append(
            f"I produced {candidate_count} {noun}; "
            f"{'it is' if candidate_count == 1 else 'they are'} shown below."
        )
    if failed:
        names = ", ".join(result.tool.replace("_", " ") for result in failed)
        answer_parts.append(f"Some planned work did not complete: {names}.")
    if not answer_parts:
        answer_parts.append("I completed the available work; its results are shown below.")

    return ReflectJudgment(
        route="finish",
        keep_results=True,
        feedback="Review output was malformed; preserved the completed tool results.",
        answer="\n\n".join(answer_parts),
    )


def reflect(state: OrchestratorState) -> dict:
    """LangGraph node: plan + results -> {"judgment": ReflectJudgment, "iteration": n+1}."""
    plan: Plan = state.get("plan") or Plan()
    ctx: Context = state.get("context") or Context()
    results = state.get("results") or []

    # Which round this is, and how many there can be. `_route` enforces the cap over the
    # judgment, so without this reflect can ask for another round it will not get and word
    # its answer as though one were coming.
    round_now = state.get("iteration", 0) + 1
    log_event("orchestrator review started", round=round_now)
    user_message = (
        f"**Round {round_now} of {MAX_ITERATIONS}.**"
        + (" This is the last round — whatever you decide, this answer is final.\n\n"
           if round_now >= MAX_ITERATIONS else "\n\n")
        + f"**Earlier in this conversation:**\n{history_view(state.get('history') or [])}\n\n"
        f"**User asked:** {state.get('message', '')}\n\n"
        f"**Goal:** {plan.goal}\n\n"
        f"**Done criteria:**\n" + "\n".join(f"- {c}" for c in plan.done_criteria) + "\n\n"
        f"**Context that was loaded:**\n{context_view(ctx)}\n\n"
        f"**Steps that ran, and what they returned:**\n"
        f"{results_view(plan.steps, results, state.get('carried', 0))}"
    )

    try:
        judgment = chat_with_structured_output(
            get_llm("cheap"), _SYSTEM, user_message, ReflectJudgment
        )
    except ValueError as exc:
        if not _format_failure(exc):
            raise
        judgment = _fallback_judgment(results)
        log_event(
            "orchestrator review fallback",
            round=round_now,
            reason=str(exc),
            candidates=sum(len(result.ideas) for result in results if result.ok),
            failed_steps=sum(1 for result in results if not result.ok),
        )
    log_event(
        "orchestrator review finished",
        round=round_now,
        route=judgment.route,
        keep_results=judgment.keep_results,
        feedback=judgment.feedback,
    )
    return {"judgment": judgment, "iteration": state.get("iteration", 0) + 1}
