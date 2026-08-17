"""Reflect (LLM, cheap) — did the plan meet its own criteria, and what now?

A check rather than a judgment call, so it runs on the cheap model. It also drafts the
message the user sees, since it is the step that knows both what was asked and what came
back.

It gets the same context summary the planner got. Without it, a run where the tools
returned nothing is indistinguishable from a run where nothing was selected, and it
reports the wrong one.
"""

from __future__ import annotations

import json

from src.common.schemas.context import Context
from src.common.utils.prompts import load_prompt
from src.llm.registry import chat_with_structured_output, get_llm
from src.agents.orchestrator_agent.context import summarise
from src.agents.orchestrator_agent.schemas import Plan, ReflectJudgment
from src.agents.orchestrator_agent.state import OrchestratorState

_SYSTEM = load_prompt(__file__)
_RESULTS_CHARS = 6000


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
        f"**What the tools returned:**\n"
        f"{json.dumps([r.model_dump() for r in results], indent=2, default=str)[:_RESULTS_CHARS]}"
    )

    judgment = chat_with_structured_output(
        get_llm("cheap"), _SYSTEM, user_message, ReflectJudgment
    )
    return {"judgment": judgment, "iteration": state.get("iteration", 0) + 1}
