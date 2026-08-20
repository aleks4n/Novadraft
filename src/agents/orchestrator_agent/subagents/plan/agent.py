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

from src.common.utils.prompts import load_prompt
from src.llm.registry import chat_with_structured_output, get_llm
from src.common.schemas.context import Context
from src.agents.orchestrator_agent.context import summarise
from src.agents.orchestrator_agent.render import results_view
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
    # `results` is carried or cleared here, not in execute: reflect decides whether the
    # previous round survives, and this is the node that starts the next one.
    kept = (list(state.get("results") or [])
            if previous is not None and previous.keep_results else [])

    if previous is not None and previous.route == "replan":
        # A second pass. It sees reflect's instruction and, when the previous round was
        # kept, exactly what is already in hand — so it plans the remainder rather than
        # the whole thing again.
        previous_plan: Plan = state.get("plan") or Plan()
        user_message += (
            f"\n\n**Your previous plan did not satisfy its criteria.**\n"
            f"{previous.feedback}\n\n"
            + (f"**Already in hand — do not produce these again:**\n"
               f"{results_view(previous_plan.steps, kept, state.get('carried', 0))}"
               if kept else
               "**The previous round was discarded.** Nothing is in hand; plan from scratch.")
        )

    return {
        "plan": chat_with_structured_output(get_llm("cheap"), _SYSTEM, user_message, Plan),
        "results": kept,
        "carried": len(kept),
    }
