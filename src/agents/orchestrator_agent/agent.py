"""Orchestrator agent — turns one user turn into tool calls, and the user's choice into
idea nodes.

One path through the graph:

    assemble_context -> plan -> execute -> reflect -> finish
                          ^        |
                          +--------+   (replan / continue)

A run always completes. It ends by *offering* draft ideas (written to the session's run
result, returned to the chat); accepting them is `commit_selection()`, a plain function
with no LLM in it. Nothing suspends mid-graph, so this reuses the same one-run-per-call /
GraphRunLog shape as the validator and ranking agents.

Standalone: `python src/agents/orchestrator_agent/agent.py "<message>" [category] [gap_id ...]`
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

from langgraph.graph import END, START, StateGraph

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.common.schemas.context import Context
from src.common.utils.logging.runlog import GraphRunLog
from src.agents.orchestrator_agent import session
from src.agents.orchestrator_agent.context import assemble_context
from src.agents.orchestrator_agent.schemas import (
    FocusSelection,
    OrchestratorInput,
    OrchestratorOutput,
    Plan,
    ReflectJudgment,
)
from src.agents.orchestrator_agent.state import OrchestratorState
from src.agents.orchestrator_agent.subagents.plan.agent import plan
from src.agents.orchestrator_agent.subagents.reflect.agent import reflect
from src.agents.orchestrator_agent.tools.commit import commit_selection  # noqa: F401 — public API
from src.agents.orchestrator_agent.tools.registry import ToolEnv, dispatch

_MAX_ITERATIONS = 3  # reflect loops before finish is forced


def execute(state: OrchestratorState) -> dict:
    """LangGraph node: run the plan's steps. Deterministic — it decides nothing.

    Steps run in order; a failing step is recorded and the rest continue, so reflect can
    see a partial result and replan rather than the whole turn dying.
    """
    plan_: Plan = state.get("plan") or Plan()
    ctx: Context = state.get("context") or Context()
    env = ToolEnv(
        category_slug=state.get("category_slug", ""),
        session_id=state.get("session_id", ""),
        run_id=state.get("run_id", ""),
    )
    results = list(state.get("results") or [])  # keep earlier waves on a `continue` loop
    results.extend(dispatch(step, ctx, env) for step in plan_.steps)
    return {"results": results}


def _fallback_answer(state: OrchestratorState) -> str:
    """Reflect leaves `answer` empty when it was routing rather than replying; if the loop
    then ends on the iteration cap, the user would get nothing. Say what happened."""
    failed = [r for r in state.get("results") or [] if not r.ok]
    if failed:
        return f"Something went wrong running the plan: {failed[0].error}"
    return ("The plan ran but produced nothing to show. Try rephrasing, or pick a gap "
            "or idea in the panel to work from.")


def finish(state: OrchestratorState) -> dict:
    """LangGraph node: end the run by offering what it produced.

    Drafts are persisted under the session so `commit_selection` can later write exactly
    what the user was shown. Nothing reaches the idea graph here.

    Provenance is stamped here rather than in the tools: a subagent knows which lens
    produced an idea, but not which session or run it was called from.
    """
    judgment: ReflectJudgment = state.get("judgment") or ReflectJudgment()
    session_id, run_id = state["session_id"], state["run_id"]

    ideas = []
    for result in state.get("results") or []:
        for idea in result.ideas:
            origin = idea.origin.model_copy(update={"session_id": session_id, "run_id": run_id})
            ideas.append(idea.model_copy(update={"origin": origin}))

    answer = judgment.answer or _fallback_answer(state)
    session.write_result(
        state["category_slug"], session_id, run_id, answer=answer, ideas=ideas
    )
    # session_id / run_id are already in state; echoing them here puts them in this
    # node's update delta, which is what the app's run manager watches to learn where
    # the drafts were written (so the UI can offer them and later commit them).
    return {"ideas": ideas, "answer": answer, "session_id": session_id, "run_id": run_id}


def _route(state: OrchestratorState) -> str:
    """Reflect's verdict, with the iteration cap as a hard override."""
    if state.get("iteration", 0) >= _MAX_ITERATIONS:
        return "finish"
    judgment = state.get("judgment") or ReflectJudgment()
    return {"continue": "execute", "replan": "plan"}.get(judgment.route, "finish")


def build_orchestrator_graph():
    """Wire the orchestrator into a compiled graph."""
    g = StateGraph(OrchestratorState)
    g.add_node("assemble_context", assemble_context)
    g.add_node("plan", plan)
    g.add_node("execute", execute)
    g.add_node("reflect", reflect)
    g.add_node("finish", finish)

    g.add_edge(START, "assemble_context")
    g.add_edge("assemble_context", "plan")
    g.add_edge("plan", "execute")
    g.add_edge("execute", "reflect")
    g.add_conditional_edges(
        "reflect", _route, {"execute": "execute", "plan": "plan", "finish": "finish"}
    )
    g.add_edge("finish", END)
    return g.compile()


_GRAPH = None


def _get_graph():
    global _GRAPH
    if _GRAPH is None:
        _GRAPH = build_orchestrator_graph()
    return _GRAPH


def _ids(value: str | list[str] | None) -> list[str]:
    """The dev-UI form gives one text box, so accept 'a, b' as well as a real list."""
    if not value:
        return []
    if isinstance(value, str):
        return [v.strip() for v in value.replace(",", " ").split() if v.strip()]
    return list(value)


def _graph_inputs(payload: OrchestratorInput, run_id: str) -> OrchestratorState:
    return {
        "session_id": payload.session_id,
        "category_slug": payload.category_slug,
        "run_id": run_id,
        "message": payload.message,
        "focus": payload.focus,
        "iteration": 0,
    }


def input_adapter(inputs: dict) -> dict:
    """Dev-harness form values -> graph state (registered via MANIFEST).

    The harness streams `build_orchestrator_graph()` directly, so it needs the same
    inputs `run()` would have built — including a `run_id`, which the form has no field
    for.
    """
    payload = OrchestratorInput(
        session_id=inputs.get("session_id") or _new_session_id(),
        category_slug=inputs["category_slug"],
        message=inputs.get("message", ""),
        focus=FocusSelection(
            gap_ids=_ids(inputs.get("gap_ids")), idea_ids=_ids(inputs.get("idea_ids"))
        ),
    )
    return _graph_inputs(payload, f"run_{uuid.uuid4().hex[:8]}")


def _new_session_id() -> str:
    return f"session_{uuid.uuid4().hex[:8]}"


def run_turn(payload: OrchestratorInput, *, stream: bool = True,
             echo: bool | None = None) -> OrchestratorOutput:
    """Handle one user turn and return what the chat should render.

    By default streams through a GraphRunLog, writing the per-run `.jsonl` to
    `workspace/<cat>/logs/orchestrator_agent/`. Pass `stream=False` for a quiet
    `.invoke()`.
    """
    run_id = f"run_{uuid.uuid4().hex[:8]}"
    inputs = _graph_inputs(payload, run_id)

    if not stream:
        _get_graph().invoke(inputs)
    else:
        if echo is None:
            echo = sys.stderr.isatty()
        rl = GraphRunLog("orchestrator_agent", workspace=payload.category_slug, run_hint=run_id)
        rl.run(_get_graph(), inputs, echo=echo)
        if rl.error:
            raise RuntimeError(rl.error)

    # Read the outcome back from the session file rather than from the graph's return:
    # GraphRunLog.run() reports the output it was handed, and the streaming path hands it
    # none. Same shape as the validator re-reading its finalized gap.
    return OrchestratorOutput.model_validate(
        session.read_result(payload.category_slug, payload.session_id, run_id)
    )


def run(category_slug: str, message: str = "", session_id: str = "",
        gap_ids: str | list[str] = "", idea_ids: str | list[str] = "",
        stream: bool = True) -> dict:
    """Flat-argument entry point — what the dev harness and the app backend call.

    Returns JSON (not a model) because the harness renders the result directly.
    `session_id` is generated when omitted, so a one-off run needs no ceremony.
    """
    payload = OrchestratorInput(
        session_id=session_id or _new_session_id(),
        category_slug=category_slug,
        message=message,
        focus=FocusSelection(gap_ids=_ids(gap_ids), idea_ids=_ids(idea_ids)),
    )
    out = run_turn(payload, stream=stream)
    return {"session_id": payload.session_id, **out.model_dump(mode="json")}


if __name__ == "__main__":
    import json

    # CLI: "<message>" [category_slug] [gap_id ...]
    message = sys.argv[1] if len(sys.argv) > 1 else "Give me some ideas for this gap."
    category = sys.argv[2] if len(sys.argv) > 2 else "transformers"
    print(json.dumps(run(category, message, gap_ids=sys.argv[3:]), indent=2, ensure_ascii=False))
