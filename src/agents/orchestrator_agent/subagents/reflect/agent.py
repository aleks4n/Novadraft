"""Reflect (LLM, cheap) — did the plan meet its own criteria, and what now?

A check rather than a judgment call, so it runs on the cheap model. It also drafts the
message the user sees, since it is the step that knows both what was asked and what came
back — but only on turns where tools actually ran. A turn that needed none is answered by
the planner and routed straight to finish, because there would be nothing here to check.

It gets the same context summary the planner got. Without it, a run where the tools
returned nothing is indistinguishable from a run where nothing was selected, and it
reports the wrong one. It also sees the steps themselves, not only their output: "the
approach was wrong" is not a verdict that can be reached from results alone.
"""

from __future__ import annotations

from src.common.schemas.context import Context
from src.common.utils.prompts import load_prompt
from src.llm.registry import chat_with_structured_output, get_llm
from src.agents.orchestrator_agent.context import summarise
from src.agents.orchestrator_agent.render import results_view
from src.agents.orchestrator_agent.schemas import Plan, ReflectJudgment
from src.agents.orchestrator_agent.state import OrchestratorState

_SYSTEM = load_prompt(__file__, "../voice.md") + "\n" + load_prompt(__file__)


def reflect(state: OrchestratorState) -> dict:
    """LangGraph node: plan + results -> {"judgment": ReflectJudgment, "iteration": n+1}."""
    plan: Plan = state.get("plan") or Plan()
    ctx: Context = state.get("context") or Context()
    results = state.get("results") or []

    user_message = (
        f"**User asked:** {state.get('message', '')}\n\n"
        f"**Goal:** {plan.goal}\n\n"
        f"**Done criteria:**\n" + "\n".join(f"- {c}" for c in plan.done_criteria) + "\n\n"
        f"**Context that was loaded:**\n{summarise(ctx)}\n\n"
        f"**Steps that ran, and what they returned:**\n"
        f"{results_view(plan.steps, results, state.get('carried', 0))}"
    )

    judgment = chat_with_structured_output(
        get_llm("cheap"), _SYSTEM, user_message, ReflectJudgment
    )
    return {"judgment": judgment, "iteration": state.get("iteration", 0) + 1}
