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
from src.common.utils.logging.trace import log_event
from src.llm.registry import chat_with_structured_output, get_llm
from src.common.schemas.context import Context
from src.agents.orchestrator_agent.render import context_view, history_view, results_view
from src.agents.orchestrator_agent.schemas import Plan, ToolCall
from src.agents.orchestrator_agent.state import OrchestratorState
from src.agents.orchestrator_agent.tools.registry import catalog

# `../voice.md` is what the planner and reflect share: what this product is, and how to
# write to the user. Both can end up being the one who speaks, so the framing lives in one
# file rather than drifting between two prompts.
_SYSTEM = load_prompt(__file__, "../voice.md") + "\n" + load_prompt(__file__)


def _consolidate_investigator_steps(planned: Plan) -> Plan:
    """Collapse repeated research calls into one retrieval-and-synthesis request.

    The planner is prompted to do this itself, but this deterministic boundary prevents a
    verbose plan from launching several complete sequential Investigator graphs. Step
    dependencies are remapped so later thinker calls still consume the combined findings.
    """
    positions = [i for i, step in enumerate(planned.steps, 1)
                 if step.tool == "investigator"]
    if len(positions) <= 1:
        return planned

    first = positions[0]
    investigator_steps = [planned.steps[i - 1] for i in positions]
    queries = [str(step.args.get("query") or "").strip()
               for step in investigator_steps]
    queries = [query for query in queries if query]
    sources: list[str] = []
    for step in investigator_steps:
        for source in step.args.get("sources") or []:
            value = str(source).strip()
            if value and value not in sources:
                sources.append(value)
    modes = {step.args.get("mode") for step in investigator_steps
             if step.args.get("mode")}
    mode = modes.pop() if len(modes) == 1 else "paper_search"
    if sources and len(sources) == 1 and mode in {None, "paper"}:
        mode = "paper"
    elif sources:
        mode = "paper_search"
    count = min(5, max(len(sources), sum(
        int(step.args.get("paper_count") or 1) for step in investigator_steps
    )))
    combined = ToolCall(
        tool="investigator",
        args={
            "query": "\n\n".join(
                f"Research objective {i}: {query}" for i, query in enumerate(queries, 1)
            ) or planned.goal,
            "mode": mode,
            "sources": sources,
            "paper_count": count,
        },
        why="Consolidated research, direct retrieval, comparison, and synthesis in one pass.",
        needs=sorted({need for step in investigator_steps for need in step.needs
                      if need not in positions}),
    )

    removed = set(positions[1:])
    old_to_new: dict[int, int] = {}
    new_steps: list[ToolCall] = []
    for old_index, step in enumerate(planned.steps, 1):
        if old_index in removed:
            old_to_new[old_index] = len(new_steps) or first
            continue
        new_step = combined if old_index == first else step
        new_steps.append(new_step)
        old_to_new[old_index] = len(new_steps)
    consolidated_index = old_to_new[first]
    old_to_new.update({position: consolidated_index for position in positions})
    remapped = [step.model_copy(update={
        "needs": sorted({old_to_new.get(need, need) for need in step.needs})
    }) for step in new_steps]
    return planned.model_copy(update={"steps": remapped})


def plan(state: OrchestratorState) -> dict:
    """LangGraph node: message + context -> {"plan": Plan}."""
    ctx: Context = state.get("context") or Context()
    previous = state.get("judgment")
    round_now = state.get("iteration", 0) + 1
    log_event("orchestrator planning", round=round_now)

    user_message = (
        f"**Earlier in this conversation:**\n{history_view(state.get('history') or [])}\n\n"
        f"**User message:**\n{state.get('message', '')}\n\n"
        f"**Selected in the UI:**\n{context_view(ctx)}\n\n"
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

    planned = chat_with_structured_output(get_llm("cheap"), _SYSTEM, user_message, Plan)
    planned = _consolidate_investigator_steps(planned)
    log_event("orchestrator plan", round=round_now, **planned.model_dump(mode="json"))
    return {
        "plan": planned,
        "results": kept,
        "carried": len(kept),
    }
