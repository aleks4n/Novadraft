"""Plan (LLM, cheap) — the user's message plus context into a tool plan.

Deliberately unconstrained in *how* it composes tools: it sees the goal and the tool
catalogue and decides. The one thing it must always produce is `done_criteria`, since
that is all reflect has to judge against.

Runs on `cheap` (deepseek-chat), not the reasoner. Measured over repeated runs: the
reasoner answers in prose instead of emitting the tool call often enough to fail
outright ~1 in 3 times even with the registry's re-ask, and it is slower (9-11s vs
5-9s) — while producing the same plan shape. Revisit if plans get more complex.
"""

from __future__ import annotations

import json

from src.common.utils.prompts import load_prompt
from src.llm.registry import chat_with_structured_output, get_llm
from src.common.schemas.context import Context
from src.agents.orchestrator_agent.context import summarise
from src.agents.orchestrator_agent.schemas import Plan
from src.agents.orchestrator_agent.state import OrchestratorState
from src.agents.orchestrator_agent.tools.registry import catalog

# `../voice.md` is what the planner and reflect share: what this product is, and how to
# write to the user. Both can end up being the one who speaks, so the framing lives in one
# file rather than drifting between two prompts.
_SYSTEM = load_prompt(__file__, "../voice.md") + "\n" + load_prompt(__file__)


def plan(state: OrchestratorState) -> dict:
    """LangGraph node: message + context -> {"plan": Plan}."""
    ctx: Context = state.get("context") or Context()
    previous = state.get("judgment")

    user_message = (
        f"**User message:**\n{state.get('message', '')}\n\n"
        f"**Selected in the UI:**\n{summarise(ctx)}\n\n"
        f"**Tools:**\n{catalog()}"
    )
    if previous is not None and previous.route == "replan":
        # A second pass: say what went wrong, so it doesn't replan into the same plan.
        results = state.get("results") or []
        user_message += (
            f"\n\n**Your previous plan did not satisfy its criteria.**\n"
            f"{previous.reasoning}\n"
            f"Step outcomes: {json.dumps([r.model_dump() for r in results], default=str)[:1500]}"
        )

    # `results` is cleared here, not in execute: a new plan is a fresh attempt, so the
    # previous one's output must not be offered alongside it. On a `continue` route the
    # graph goes straight back to execute and results accumulate, which is what we want.
    return {
        "plan": chat_with_structured_output(get_llm("cheap"), _SYSTEM, user_message, Plan),
        "results": [],
    }
