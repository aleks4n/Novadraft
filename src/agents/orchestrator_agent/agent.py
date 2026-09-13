"""Orchestrator agent — turns one user turn into tool calls, and the user's choice
into idea nodes.

One path through the graph::

    assemble_context -> plan -> execute -> reflect -> finish
             |           | ^                  |         ^
             |           | +---- replan ------+         |
             |           +-- plan called no tools ------+
             |           +-- awaiting approval --> propose -> END
             +-- an approved plan came in --> adopt_plan --> execute

A turn that needs no tools is answered by the planner itself and skips to
``finish``: there is nothing to dispatch, and reflect has no done-criteria to check
against.

A turn that does call tools is shown to the user first. The run ends at
``propose``, having written its plan to the session; the user adjusts the arguments
the planner chose and presses Start, which begins a second run carrying that plan
as ``approved_plan``. That run re-assembles the context, adopts the plan without
planning again, and executes it. Pending state lives in the session file rather
than in a suspended graph.

A run always completes. It ends by offering draft ideas (written to the session's
run result, returned to the chat); accepting them is ``commit_selection()``, a
plain function with no LLM in it.

Standalone: ``python src/agents/orchestrator_agent/agent.py "<message>" [category] [gap_id ...]``
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
from src.common.schemas.idea import IdeaRecord
from src.common.utils.logging.trace import log_event
from src.common.utils.logging.runlog import GraphRunLog
from src.agents.orchestrator_agent import session
from src.agents.orchestrator_agent.context import assemble_context
from src.agents.orchestrator_agent.schemas import (
    FocusSelection,
    OrchestratorInput,
    OrchestratorOutput,
    Plan,
    ReflectJudgment,
    ToolCall,
    ToolResult,
    parse_step_ref,
)
from src.agents.orchestrator_agent.state import (
    MAX_ITERATIONS,
    MAX_LLM_CALLS_PER_TURN,
    UNMETERED_TOOL_LLM_CHARGE,
    OrchestratorState,
)
from src.agents.orchestrator_agent.subagents.plan.agent import (
    normalize_thinker_plan,
    plan,
)
from src.agents.orchestrator_agent.subagents.reflect.agent import reflect
from src.agents.orchestrator_agent.tools.commit import commit_selection  # noqa: F401 — public API
from src.agents.orchestrator_agent.tools.registry import ToolEnv, dispatch


def _context_for(step: ToolCall, base: Context,
                 produced: dict[int, ToolResult]) -> tuple[Context, str]:
    """The context one step runs against, and a reason to skip it (empty string = run).

    A step that declares `needs` works on what those steps produced, not on what the user
    selected: the planner said "refine the output of step 1", so that output is the
    subject and it replaces `ideas`. Gaps and papers stay — they are background either way.

    Without this, a `generate -> refine` plan reads `context.ideas` from the up-front
    assembly, finds it empty, and quietly refines nothing.
    """
    if not step.needs:
        return base, ""

    ideas: list[IdeaRecord] = []
    findings: list[str] = []
    for n in step.needs:
        earlier = produced.get(n)
        if earlier is None:
            return base, f"needs step {n}, which is not an earlier step"
        if not earlier.ok:
            return base, f"needs step {n}, which failed: {earlier.error}"
        ideas.extend(earlier.ideas)
        if earlier.text:
            findings.append(earlier.text)

    if not ideas and not findings:
        return base, f"needs step(s) {step.needs}, which produced nothing to work from"

    # Ideas replace the selection — the planner named those steps as the subject. Findings
    # accumulate: research adds to what is known, it does not displace it.
    update: dict = {"findings": base.findings + findings} if findings else {}
    if ideas:
        update["ideas"] = ideas
    return base.model_copy(update=update), ""


def execute(state: OrchestratorState) -> dict:
    """LangGraph node: run the plan's steps. Deterministic — it decides nothing.

    Steps run in order, each against the context `_context_for` builds for it. A failing
    or skipped step is recorded and the rest continue, so reflect can see a partial result
    and replan rather than the whole turn dying.

    Order is the plan's own; `needs` declares data flow, not scheduling. Nothing here runs
    concurrently, and with one or two steps per plan there is nothing to gain by it.
    """
    plan_: Plan = state.get("plan") or Plan()
    ctx: Context = state.get("context") or Context()
    results = list(state.get("results") or [])  # rounds reflect chose to keep
    produced: dict[int, ToolResult] = {}  # this pass only, by 1-based step number
    at: dict[int, int] = {}  # step number -> where its result sits in `results`
    consumed: set[str] = set()  # `step#position` handles a later step rewrote
    spent = state.get("spent", 0)

    for i, step in enumerate(plan_.steps, 1):
        log_event(
            "orchestrator step started",
            step=i,
            total=len(plan_.steps),
            tool=step.tool,
            why=step.why,
        )
        # Each step gets an equal share of what the turn has left, recomputed as it goes:
        # a step that came in under its slice leaves the rest for the ones after it, and a
        # replan inherits whatever the earlier rounds did not use.
        remaining = max(0, MAX_LLM_CALLS_PER_TURN - spent)
        steps_left = len(plan_.steps) - i + 1
        granted = max(1, remaining // steps_left)
        env = ToolEnv(
            category_slug=state.get("category_slug", ""),
            session_id=state.get("session_id", ""),
            run_id=state.get("run_id", ""),
            max_llm_calls=granted,
            chained={n: list(produced[n].ideas) for n in step.needs if n in produced},
        )
        step_ctx, skip = _context_for(step, ctx, produced)
        result = (ToolResult(tool=step.tool, ok=False, error=f"skipped — {skip}")
                  if skip else dispatch(step, step_ctx, env))
        # Charge what it reported, or a conservative fixed amount when it reported
        # nothing. A subagent that keeps no ledger — the investigator keeps none — must
        # not be free, but charging its whole slice makes a single-step plan exhaust the
        # entire turn and prevents reflect's requested retry from ever running.
        # A skipped step ran nothing and is charged nothing.
        reported = int(((result.envelope.get("metrics") or {}).get("llm_calls")) or 0)
        spent += 0 if skip else (reported or min(granted, UNMETERED_TOOL_LLM_CHARGE))
        if result.consumed:
            consumed.add(result.consumed)
        produced[i] = result
        at[i] = len(results)
        results.append(result)
        log_event(
            "orchestrator step finished",
            step=i,
            total=len(plan_.steps),
            tool=step.tool,
            ok=result.ok,
            error=result.error,
            ideas=len(result.ideas),
            has_text=bool(result.text),
        )

    return {"results": _drop_superseded(results, at, consumed), "spent": spent}


def _drop_superseded(results: list[ToolResult], at: dict[int, int],
                     consumed: set[str]) -> list[ToolResult]:
    """Stop offering the drafts that a later step rewrote.

    A refinement returns *its subject, rewritten* — not an additional idea. Left alone,
    a `generate -> refine` plan puts the same idea on screen twice, before and after,
    with nothing marking which supersedes which; accepting both writes one idea to the
    graph as two records.

    Done here rather than in `finish` because the step numbering is only meaningful
    within one pass: results kept from an earlier round sit in front of this round's, and
    a handle recorded then would point at the wrong record now.
    """
    if not consumed:
        return results
    # Grouped by step and removed in one pass: taking them out one at a time renumbers
    # the rest, so the second removal from the same step would hit the wrong record.
    by_step: dict[int, set[int]] = {}
    for handle in consumed:
        parsed = parse_step_ref(handle)
        if parsed is not None and parsed[0] in at:
            by_step.setdefault(parsed[0], set()).add(parsed[1])

    out = list(results)
    for step, positions in by_step.items():
        source = out[at[step]]
        keep = [index for index in range(len(source.ideas)) if index + 1 not in positions]
        out[at[step]] = source.model_copy(update={
            "ideas": [source.ideas[i] for i in keep],
            # `draft_index` stays parallel, so what is left still points at the right
            # record inside the envelope rather than sliding up by one.
            "draft_index": [source.draft_index[i] for i in keep
                            if i < len(source.draft_index)],
        })
    return out


def _fallback_answer(state: OrchestratorState) -> str:
    """Last resort when neither LLM wrote a reply.

    Reflect leaves `answer` empty when it was routing rather than replying, and if the
    loop then ends on the iteration cap the user would get nothing. The planner can do
    the same on a conversational turn by emitting no steps and no answer. Say what
    happened rather than returning an empty message.
    """
    failed = [r for r in state.get("results") or [] if not r.ok]
    if failed:
        return f"Something went wrong running the plan: {failed[0].error}"
    if not (state.get("plan") or Plan()).steps:
        return ("I didn't catch what you'd like to do. Pick a gap or an idea in the "
                "panel, or tell me what you want to work on.")
    return ("The plan ran but produced nothing to show. Try rephrasing, or pick a gap "
            "or idea in the panel to work from.")


def finish(state: OrchestratorState) -> dict:
    """LangGraph node: end the run by offering what it produced.

    Drafts are persisted under the session so `commit_selection` can later write exactly
    what the user was shown. Nothing reaches the idea graph here.

    Provenance is not re-stamped: the thinker already sets `origin` on what it produced —
    the run, the node in its trace, the stance — and the session that run belonged to is
    carried by the run entry and by the session file the drafts land in. Overwriting it
    here would replace what the thinker knows with less.
    """
    judgment: ReflectJudgment = state.get("judgment") or ReflectJudgment()
    plan_: Plan = state.get("plan") or Plan()
    session_id, run_id = state["session_id"], state["run_id"]

    # An offer is a draft plus where it came from. Accepting needs the envelope that
    # produced it — the thinking graph sits on the envelope, not on the candidate — so the
    # provenance is recorded now, while it is still known, rather than reconstructed later
    # from a pooled list that has lost which step made what.
    ideas: list[IdeaRecord] = []
    envelopes: list[dict] = []
    offers: list[dict] = []
    for result in state.get("results") or []:
        if not result.ideas:
            continue
        envelope_index = len(envelopes)
        envelopes.append(result.envelope)
        for position, idea in enumerate(result.ideas):
            slot = (result.draft_index[position]
                    if position < len(result.draft_index) else position)
            offers.append({"envelope": envelope_index, "draft": slot})
            ideas.append(idea)

    # Whoever saw the last decision-relevant thing speaks: reflect when tools ran (it is
    # the only step that saw the results), the planner when none did (it is the only step
    # that saw the tool catalogue).
    #
    # A planner answer written alongside steps is dropped rather than used as a fallback:
    # it was composed before execution, so on a run that then failed it would tell the
    # user what was about to happen as though it had.
    planned_reply = "" if plan_.steps else plan_.answer
    answer = judgment.answer or planned_reply or _fallback_answer(state)
    # Reflect may request another pass, but the deterministic guards have the final word.
    # Make that visible instead of returning prose such as "I'm running a deeper pass"
    # after the graph has actually ended.
    if judgment.route == "replan":
        if state.get("iteration", 0) >= MAX_ITERATIONS:
            answer = (answer.rstrip() + "\n\nI could not run another pass because this "
                      "turn reached its maximum number of research rounds.")
        elif state.get("spent", 0) >= MAX_LLM_CALLS_PER_TURN:
            answer = (answer.rstrip() + "\n\nI could not run another pass because this "
                      "turn reached its research budget.")
    session.write_result(
        state["category_slug"], session_id, run_id,
        message=state.get("message", ""), answer=answer, ideas=ideas,
        envelopes=envelopes, offers=offers,
        # The plan as it actually ran — including a replan's rewrite of it, and the
        # arguments the user edited before starting it. The run log has the same thing
        # step by step; this is the copy that outlives it.
        plan=plan_.model_dump(mode="json"),
    )
    # session_id / run_id are already in state; echoing them here puts them in this
    # node's update delta, which is what the app's run manager watches to learn where
    # the drafts were written (so the UI can offer them and later commit them).
    return {"ideas": ideas, "answer": answer, "session_id": session_id, "run_id": run_id}


def propose(state: OrchestratorState) -> dict:
    """LangGraph node: end the run by offering the plan instead of running it.

    The mirror of `finish`. That one offers drafts the user accepts; this one offers the
    plan the user starts — and in both cases what is offered is written to the session
    file, because the decision happens in a later HTTP call and nothing may sit in memory
    waiting for it.

    Nothing has been dispatched at this point, so there is no answer to write: the reply
    the user sees is the plan itself.
    """
    plan_: Plan = state.get("plan") or Plan()
    log_event(
        "orchestrator plan proposed",
        goal=plan_.goal,
        steps=len(plan_.steps),
        tools=[step.tool for step in plan_.steps],
    )
    session.write_result(
        state["category_slug"], state["session_id"], state["run_id"],
        message=state.get("message", ""), answer="",
        status="awaiting_approval", plan=plan_.model_dump(mode="json"),
    )
    return {"answer": "", "ideas": [],
            "session_id": state["session_id"], "run_id": state["run_id"]}


def adopt_plan(state: OrchestratorState) -> dict:
    """LangGraph node: take the plan the user started, unchanged, as this run's plan.

    Deterministic — the planning already happened, in the run that proposed it, and the
    user has since had the last word on the arguments. Re-planning here would throw that
    away and could easily come back with a different plan than the one they approved.

    It re-emits `orchestrator plan` so everything downstream of the planner — the progress
    view, the reflect prompt's view of what was asked for — sees the plan the same way on
    both runs, rather than the executing run appearing to run steps nobody planned.
    """
    # Unchanged in intent, normalized in arithmetic: the gate lets the researcher edit
    # arguments, and a `width` typed onto a refinement step brings a candidate count with
    # it that refinement cannot return. Left in, that count is a permanent shortfall which
    # sends reflect back to the planner and re-runs the whole tree of thought.
    approved: Plan = normalize_thinker_plan(state.get("approved_plan") or Plan())
    log_event("orchestrator plan approved", goal=approved.goal, steps=len(approved.steps))
    log_event("orchestrator plan", round=1, **approved.model_dump(mode="json"))
    return {"plan": approved}


def _entry(state: OrchestratorState) -> str:
    """Plan this turn, or run the plan the user already approved."""
    return "adopt_plan" if (state.get("approved_plan") or Plan()).steps else "plan"


def _after_plan(state: OrchestratorState) -> str:
    """Skip execute and reflect when the plan calls no tools; stop for approval otherwise.

    A conversational turn is already answered by then (`Plan.answer`), there is nothing
    to dispatch, and reflect — which checks results against done-criteria — would have
    neither. Routing straight to finish costs one LLM call for such a turn instead of two.
    It is also never held for approval: there is nothing to approve, and making the user
    press Start to be told the answer they already have is not a gate, it is a delay.

    Approval is asked once, for the plan that opens the turn. A replan happens inside a
    run the user already started, in response to results they have not seen yet; stopping
    there would ask them to approve a correction to a plan they never watched fail.
    """
    plan_: Plan = state.get("plan") or Plan()
    if not plan_.steps:
        return "finish"
    if state.get("require_approval") and state.get("iteration", 0) == 0:
        return "propose"
    return "execute"


def _route(state: OrchestratorState) -> str:
    """Reflect's verdict, with the iteration cap as a hard override.

    Only two destinations: back to the planner with feedback, or done. `execute` is
    reachable only from `plan`, so every wave of tool calls has a plan that asked for it.
    """
    if state.get("iteration", 0) >= MAX_ITERATIONS:
        return "finish"
    # Nothing left to spend is as good a reason to stop as too many rounds. Replanning
    # here would cost a planning call to produce a step that can only truncate, and hand
    # the user less than the round that was already in hand.
    if state.get("spent", 0) >= MAX_LLM_CALLS_PER_TURN:
        return "finish"
    judgment = state.get("judgment") or ReflectJudgment()
    return "plan" if judgment.route == "replan" else "finish"


def build_orchestrator_graph():
    """Wire the orchestrator into a compiled graph."""
    g = StateGraph(OrchestratorState)
    g.add_node("assemble_context", assemble_context)
    g.add_node("plan", plan)
    g.add_node("adopt_plan", adopt_plan)
    g.add_node("propose", propose)
    g.add_node("execute", execute)
    g.add_node("reflect", reflect)
    g.add_node("finish", finish)

    g.add_edge(START, "assemble_context")
    g.add_conditional_edges("assemble_context", _entry,
                            {"adopt_plan": "adopt_plan", "plan": "plan"})
    g.add_edge("adopt_plan", "execute")
    g.add_conditional_edges("plan", _after_plan,
                            {"execute": "execute", "propose": "propose", "finish": "finish"})
    g.add_edge("propose", END)
    g.add_edge("execute", "reflect")
    g.add_conditional_edges("reflect", _route, {"plan": "plan", "finish": "finish"})
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
    inputs: OrchestratorState = {
        "session_id": payload.session_id,
        "category_slug": payload.category_slug,
        "run_id": run_id,
        "message": payload.message,
        "focus": payload.focus,
        "require_approval": payload.require_approval,
        "iteration": 0,
    }
    if payload.approved_plan is not None:
        inputs["approved_plan"] = payload.approved_plan
    return inputs


def input_adapter(inputs: dict) -> dict:
    """Dev-harness form values -> graph state (registered via MANIFEST).

    The harness streams `build_orchestrator_graph()` directly, so it needs the same
    inputs `run()` would have built — including a `run_id`, which the form has no field
    for and which is therefore minted here.

    A caller that will need to find the turn's result afterwards passes its own instead.
    The app backend does: the run file this turn writes is named by the `run_id`, and
    that file is what the chat reads the reply and the offered drafts back out of.
    """
    payload = OrchestratorInput(
        session_id=inputs.get("session_id") or _new_session_id(),
        category_slug=inputs["category_slug"],
        message=inputs.get("message", ""),
        focus=FocusSelection(
            gap_ids=_ids(inputs.get("gap_ids")), idea_ids=_ids(inputs.get("idea_ids")),
            active_ids=_ids(inputs.get("active_ids")),
        ),
        # Both travel as JSON: the run manager round-trips a run's inputs through its
        # index, so a restart has to be able to rebuild the same turn from a file.
        require_approval=_flag(inputs.get("require_approval")),
        approved_plan=(Plan.model_validate(inputs["approved_plan"])
                       if inputs.get("approved_plan") else None),
    )
    return _graph_inputs(payload, inputs.get("run_id") or f"run_{uuid.uuid4().hex[:8]}")


def _flag(value) -> bool:
    """A checkbox that came through a text box. The dev form has only text inputs, so
    "true" arrives as the string, and `bool("false")` is True."""
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


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
        active_ids: str | list[str] = "",
        require_approval: bool = False, approved_plan: dict | None = None,
        stream: bool = True) -> dict:
    """Flat-argument entry point — what the app backend calls.

    Returns JSON (not a model) because the harness renders the result directly.
    `session_id` is generated when omitted, so a one-off run needs no ceremony.
    """
    payload = OrchestratorInput(
        session_id=session_id or _new_session_id(),
        category_slug=category_slug,
        message=message,
        focus=FocusSelection(gap_ids=_ids(gap_ids), idea_ids=_ids(idea_ids),
                             active_ids=_ids(active_ids)),
        require_approval=require_approval,
        approved_plan=Plan.model_validate(approved_plan) if approved_plan else None,
    )
    out = run_turn(payload, stream=stream)
    return {"session_id": payload.session_id, **out.model_dump(mode="json")}


if __name__ == "__main__":
    import json

    # CLI: "<message>" [category_slug] [gap_id ...]
    message = sys.argv[1] if len(sys.argv) > 1 else "Give me some ideas for this gap."
    category = sys.argv[2] if len(sys.argv) > 2 else "transformers"
    print(json.dumps(run(category, message, gap_ids=sys.argv[3:]), indent=2, ensure_ascii=False))
