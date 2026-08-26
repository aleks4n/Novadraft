"""What happens on one turn — every scenario the orchestrator can take, recorded.

Each test drives a real turn through the real graph with the two LLM steps and the two
subagents replaced by recorders. What is exercised is therefore all of the orchestrator's
own logic — routing, context assembly, step chaining, budget slicing, the accept gate —
while the parts that would cost money and vary run to run are pinned.

The recorder captures **who called whom with what**, so a test asserts the call chain
rather than only the end state: that the thinker was handed exactly one idea in refinement
mode, that a chained step received the previous step's output, that a skipped step never
reached a subagent at all. `dev/record_flows.py` replays these same scenarios to write
the flows document, so the depiction and the assertions cannot drift apart.

Nothing here touches the real workspace: `NOVADRAFT_WORKSPACE` is pointed at a tmp dir per
test, seeded with one gap and one idea.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import pytest

from src.agents.creative_thinker.schemas import Envelope
from src.common.schemas.idea import (
    IdeaOrigin, IdeaRecord, RunEntry, RunHeader, ThoughtNode,
)
from src.common.utils import storage

from src.agents.orchestrator_agent import session
from src.agents.orchestrator_agent.agent import build_orchestrator_graph
from src.agents.orchestrator_agent.state import MAX_LLM_CALLS_PER_TURN
from src.agents.orchestrator_agent.schemas import (
    FocusSelection, Plan, ReflectJudgment, ToolCall,
)
from src.agents.orchestrator_agent.tools.commit import commit_selection

GAP_ID = "gap_testflow"
IDEA_A = "idea_testaaa"
IDEA_B = "idea_testbbb"
CATEGORY = "flowtest"


# --- the recorder -------------------------------------------------------------


@dataclass
class Call:
    """One call into something the orchestrator does not own."""

    who: str
    args: dict[str, Any]


@dataclass
class Recorder:
    """Stands in for the two LLM steps and the two subagents, and remembers every call.

    `plans` and `judgments` are consumed in order, so a scenario that replans supplies two
    of each. Running out is a scenario error, not a fallback: a silent default would let a
    test pass while the graph looped somewhere the scenario never described.
    """

    plans: list[Plan] = field(default_factory=list)
    judgments: list[ReflectJudgment] = field(default_factory=list)
    envelopes: list[Envelope] = field(default_factory=list)
    findings: list[str] = field(default_factory=list)
    calls: list[Call] = field(default_factory=list)

    def _next(self, queue: list, who: str):
        if not queue:
            raise AssertionError(f"{who} was called more often than the scenario describes")
        return queue.pop(0)

    def to(self, who: str) -> list[Call]:
        return [c for c in self.calls if c.who == who]

    def chain(self) -> list[str]:
        return [c.who for c in self.calls]


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """An empty workspace with one gap and one committed idea in it."""
    monkeypatch.setenv(storage.WORKSPACE_ENV, str(tmp_path))
    # `ensure_category` verifies rather than creates — categories are made by the pipeline
    # that owns them, so a test seeds its own.
    category = tmp_path / CATEGORY
    category.mkdir(parents=True, exist_ok=True)
    (category / "meta.json").write_text(json.dumps({"slug": CATEGORY, "title": "Flow tests"}))

    gap = {
        "gap_id": GAP_ID, "title": "Dot product compatibility may be suboptimal",
        "description": "Reducing d_k hurts quality.", "type": "limitation",
        "canonical_description": "dot-product compatibility may be insufficient",
        "status": "validated", "extracted_at": "2026-01-01T00:00:00Z",
        "evidence": [{"paper_id": "arxiv_1706.03762", "section": "results",
                      "anchor": "a more sophisticated compatibility function",
                      "quote": "a more sophisticated compatibility function may be beneficial.",
                      "char_offset": [0, 10], "grounding_status": "matched"}],
    }
    storage.write_gap_json(CATEGORY, gap, "master")
    for idea_id, title in ((IDEA_A, "Kernel attention"), (IDEA_B, "Low-rank bilinear form")):
        storage.write_idea_json(CATEGORY, IdeaRecord(
            idea_id=idea_id, title=title, state="root", description=f"{title} description.",
            problem="The dot product is a single bilinear form.",
            addresses=[GAP_ID], sections={"technique": ["step one", "step two"]},
        ).model_dump(mode="json"))
    return tmp_path


@pytest.fixture
def rec(monkeypatch) -> Recorder:
    """Patch the two LLM steps and the two subagents onto a recorder."""
    recorder = Recorder()

    def fake_plan_llm(_llm, _system, user, schema):
        recorder.calls.append(Call("plan.llm", {"prompt": user}))
        return recorder._next(recorder.plans, "plan")

    def fake_reflect_llm(_llm, _system, user, schema):
        recorder.calls.append(Call("reflect.llm", {"prompt": user}))
        return recorder._next(recorder.judgments, "reflect")

    def fake_think(call):
        recorder.calls.append(Call("creative_thinker", {
            "mode": call.mode,
            "task": call.task,
            "styles": [s.name for s in call.styles],
            "width": call.width,
            "depth": call.depth,
            "tool_use": call.tool_use,
            "budget_llm_calls": call.budget.max_llm_calls,
            "subject": call.idea.idea_id,
            "subject_title": call.idea.title,
            "context_gaps": [g.get("gap_id") for g in call.context.get("gaps") or []],
            "context_ideas": [i.get("idea_id") for i in call.context.get("ideas") or []],
            "context_findings": list(call.context.get("findings") or []),
            "context_user_input": [d.get("id")
                                   for d in call.context.get("user_input") or []],
            # The whole context dict as well, so a test can assert on what was actually
            # handed over rather than on the summary above. Not listed in the flows
            # document's `_SHOW`, so it stays out of the depiction.
            "context": call.context,
        }))
        return recorder._next(recorder.envelopes, "creative_thinker")

    def fake_investigator(text, *, session_id, category_slug=None, stream=True, **_):
        recorder.calls.append(Call("investigator", {
            "query": text, "session_id": session_id, "category_slug": category_slug,
        }))
        return recorder._next(recorder.findings, "investigator")

    monkeypatch.setattr(
        "src.agents.orchestrator_agent.subagents.plan.agent.chat_with_structured_output",
        fake_plan_llm)
    monkeypatch.setattr(
        "src.agents.orchestrator_agent.subagents.reflect.agent.chat_with_structured_output",
        fake_reflect_llm)
    monkeypatch.setattr("src.agents.creative_thinker.think", fake_think)
    monkeypatch.setattr("src.agents.investigator_agent.run", fake_investigator)
    return recorder


# --- helpers ------------------------------------------------------------------


def candidate(title: str, node: str = "n0", **over) -> IdeaRecord:
    """A generation candidate: no id, because it has no place in the graph yet.

    `origin` is what lets an accept carve the branch that produced it out of the run's
    trace — without it the record would be written with no history of how it was reached,
    which is the thing `apply_envelope` exists to prevent.
    """
    return IdeaRecord(idea_id=None, title=title, state="provisional",
                      description=f"{title} description.", addresses=[GAP_ID],
                      sections={"technique": ["do the thing"]},
                      origin=IdeaOrigin(run_id="run_thinker", node_id=node,
                                        node_path=[node], mode="generation"), **over)


def envelope(*, idea: IdeaRecord | None = None, drafts=(), answer="",
             status="completed", limit=None, nodes=2, llm_calls=8) -> Envelope:
    """An envelope shaped like the thinker's: the trace rides on `idea`, not on drafts.

    `llm_calls` is what the run reports having spent. A real thinker always keeps this
    ledger; passing 0 models a subagent that keeps none, which the orchestrator charges
    its whole slice rather than treating as free.
    """
    carrier = idea or IdeaRecord(idea_id=None)
    carrier = carrier.model_copy(update={"runs": [RunEntry(
        run_id="run_thinker",
        header=RunHeader(mode="generation"),
        nodes=[ThoughtNode(node_id=f"n{i}", text=f"branch {i}", kind="candidate")
               for i in range(nodes)],
    )]})
    return Envelope(idea=carrier, drafts=list(drafts), answer=answer,
                    status=status, limit=limit, metrics={"llm_calls": llm_calls})


def turn(workspace, message: str, *, gaps=(), ideas=(), session_id="s1", run_id="run_1") -> dict:
    """Run one turn through the real graph and return the final state."""
    return build_orchestrator_graph().invoke({
        "session_id": session_id, "category_slug": CATEGORY, "run_id": run_id,
        "message": message, "iteration": 0,
        "focus": FocusSelection(gap_ids=list(gaps), idea_ids=list(ideas)),
    })


def stored(session_id="s1", run_id="run_1") -> dict:
    return session.read_result(CATEGORY, session_id, run_id)


def drop(session_id="s1", *, notes=(), files=()) -> None:
    """Seed a session the way the chat surface does: notes pasted, documents dropped.

    The stored entry keeps a 400-char digest for the UI; a file's real text lives in the
    session's `files/` folder, which is what makes "the digest is not the document" a
    thing a test can tell apart.
    """
    directory = session.session_dir(CATEGORY, session_id)
    session.session_files_dir(CATEGORY, session_id).mkdir(parents=True, exist_ok=True)
    entries = []
    for index, text in enumerate(notes):
        entries.append({"kind": "note", "id": f"note_{index}", "label": "Pasted note",
                        "digest": text[:400]})
    for name, text in files:
        (session.session_files_dir(CATEGORY, session_id) / name).write_bytes(
            text.encode("utf-8") if isinstance(text, str) else text)
        entries.append({"kind": "file", "id": name, "label": name,
                        "digest": str(text)[:400]})
    (directory / "session.json").write_text(json.dumps(
        {"session_id": session_id, "category_slug": CATEGORY, "context": entries}))


# --- scenarios ----------------------------------------------------------------


def test_conversational_turn_calls_no_tool_and_skips_reflect(workspace, rec):
    """"what can you do?" — the planner answers, and execute and reflect never run.

    The reply comes from the planner rather than reflect because on a turn with no steps
    there are no results to reflect on, and the planner is the only step that sees the
    tool catalogue.
    """
    rec.plans = [Plan(goal="Say what this assistant does.", steps=[],
                      answer="I turn gaps into candidate research ideas.")]

    state = turn(workspace, "what can you do?")

    assert rec.chain() == ["plan.llm"]
    assert state["answer"] == "I turn gaps into candidate research ideas."
    assert stored()["ideas"] == []
    assert stored()["message"] == "what can you do?"


def test_generation_offers_drafts_and_writes_nothing(workspace, rec):
    """A gap is selected and the user asks for candidates.

    The thinker is handed the gap in `context.gaps` and no subject — generation has none.
    What comes back is offered, and the idea graph is untouched until an accept.
    """
    rec.plans = [Plan(goal="Generate 2 candidates.", done_criteria=["2 candidates"],
                      steps=[ToolCall(tool="creative_thinker", args={
                          "thinking_mode": "generation", "width": 2,
                          "styles": ["skeptic", "transferer"],
                          "instruction": "Fan out over the gap."})])]
    rec.envelopes = [envelope(drafts=[candidate("Kernel mixture"), candidate("Poly kernel")])]
    rec.judgments = [ReflectJudgment(route="finish", answer="I drafted two candidates.")]

    state = turn(workspace, "give me 2 ideas for this gap", gaps=[GAP_ID])

    assert rec.chain() == ["plan.llm", "creative_thinker", "reflect.llm"]
    sent = rec.to("creative_thinker")[0].args
    assert sent["mode"] == "generation"
    assert sent["styles"] == ["skeptic", "transferer"]
    assert sent["context_gaps"] == [GAP_ID]
    assert sent["subject"] is None, "generation has no subject idea"

    assert [i.title for i in state["ideas"]] == ["Kernel mixture", "Poly kernel"]
    assert all(i.idea_id is None for i in state["ideas"]), "a draft has no id yet"
    assert storage.list_idea_ids(CATEGORY) == [IDEA_A, IDEA_B], "nothing was written"


def test_refinement_hands_the_thinker_exactly_one_subject(workspace, rec):
    """Two ideas are in scope and the user means one of them.

    `work_on` names it; the adapter moves that record into `spec.idea` and leaves the
    other in `context.ideas` as background. That is what makes the thinker's contract —
    "refinement deepens ONE existing idea" — true rather than aspirational.
    """
    rec.plans = [Plan(goal="Sharpen the second idea.", done_criteria=["1 revised candidate"],
                      steps=[ToolCall(tool="creative_thinker", args={
                          "thinking_mode": "refinement", "work_on": [IDEA_B], "depth": 3,
                          "instruction": "Make it concrete."})])]
    refined = IdeaRecord(idea_id=IDEA_B, title="Low-rank bilinear form", state="root",
                         description="Now concrete.", addresses=[GAP_ID])
    rec.envelopes = [envelope(idea=refined, answer="Made it concrete.")]
    rec.judgments = [ReflectJudgment(route="finish", answer="I made the second one concrete.")]

    turn(workspace, "make the second one more concrete", ideas=[IDEA_A, IDEA_B])

    sent = rec.to("creative_thinker")[0].args
    assert sent["subject"] == IDEA_B
    assert sent["context_ideas"] == [IDEA_A], "the other idea stays as background"
    assert sent["depth"] == 3


def test_work_on_naming_an_id_not_in_context_fails_the_step(workspace, rec):
    """An id the planner invented never reaches the subagent.

    Without the check the thinker would silently work whichever idea came first, which is
    the failure the argument exists to prevent.
    """
    rec.plans = [Plan(goal="Refine.", done_criteria=["1 candidate"],
                      steps=[ToolCall(tool="creative_thinker", args={
                          "thinking_mode": "refinement", "work_on": ["idea_invented"]})])]
    rec.judgments = [ReflectJudgment(route="finish", answer="I could not find that idea.")]

    state = turn(workspace, "refine the third one", ideas=[IDEA_A])

    assert rec.to("creative_thinker") == [], "the subagent was never called"
    result = state["results"][0]
    assert result.ok is False
    assert "none of which is in context" in result.error


def test_chained_steps_pass_ideas_forward(workspace, rec):
    """`generate -> refine(needs=[1])`.

    Step 2 works on what step 1 produced. Without `needs` it would run against the
    up-front context, find no ideas, and refine nothing.
    """
    rec.plans = [Plan(goal="Generate then sharpen.", done_criteria=["2 candidates"], steps=[
        ToolCall(tool="creative_thinker", args={"thinking_mode": "generation", "width": 2,
                                                "styles": ["skeptic", "optimizer"]}),
        ToolCall(tool="creative_thinker", needs=[1],
                 args={"thinking_mode": "refinement", "instruction": "Sharpen these."}),
    ])]
    made = [candidate("From skeptic"), candidate("From optimizer")]
    rec.envelopes = [envelope(drafts=made), envelope(idea=made[0], answer="Sharpened.")]
    rec.judgments = [ReflectJudgment(route="finish", answer="Two candidates, then sharpened.")]

    turn(workspace, "give me ideas then sharpen them", gaps=[GAP_ID])

    second = rec.to("creative_thinker")[1].args
    assert second["subject_title"] == "From skeptic", "step 2 got step 1's output"


def test_a_step_whose_needs_produced_nothing_is_skipped_not_run(workspace, rec):
    """A dependency that returned nothing stops the step rather than feeding it nothing.

    Reflect is told why, so it can replan instead of reporting a shortfall it cannot
    explain.
    """
    rec.plans = [Plan(goal="Generate then sharpen.", done_criteria=["2 candidates"], steps=[
        ToolCall(tool="creative_thinker", args={"thinking_mode": "generation", "width": 2}),
        ToolCall(tool="creative_thinker", needs=[1], args={"thinking_mode": "refinement"}),
    ])]
    rec.envelopes = [envelope(drafts=[])]  # step 1 returns nothing
    rec.judgments = [ReflectJudgment(route="finish", answer="Nothing came back.")]

    state = turn(workspace, "give me ideas then sharpen them", gaps=[GAP_ID])

    assert len(rec.to("creative_thinker")) == 1, "step 2 never reached the subagent"
    assert state["results"][1].ok is False
    assert "skipped" in state["results"][1].error


def test_investigator_findings_flow_into_a_later_step(workspace, rec):
    """`investigate -> generate(needs=[1])`.

    The investigator returns prose, not ideas, so it arrives as `context.findings` rather
    than replacing the subject. Research adds to what is known; it does not displace it.
    """
    rec.plans = [Plan(goal="Research then generate.", done_criteria=["2 candidates"], steps=[
        ToolCall(tool="investigator", args={"query": "attention compatibility functions"}),
        ToolCall(tool="creative_thinker", needs=[1],
                 args={"thinking_mode": "generation", "width": 2}),
    ])]
    rec.findings = ["Three papers tried bilinear forms; none beat dot product at scale."]
    rec.envelopes = [envelope(drafts=[candidate("Avoids bilinear")])]
    rec.judgments = [ReflectJudgment(route="finish", answer="Looked it up, then drafted one.")]

    turn(workspace, "what's been tried? then give me ideas", gaps=[GAP_ID])

    asked = rec.to("investigator")[0].args
    assert asked["category_slug"] == CATEGORY, "fetches land in this session, not a legacy path"
    assert asked["session_id"] == "s1"

    sent = rec.to("creative_thinker")[0].args
    assert "bilinear forms" in sent["context_findings"][0]
    assert sent["context_gaps"] == [GAP_ID], "the gap is still there — findings add, not replace"


def test_replan_keeping_results_accumulates_instead_of_repeating(workspace, rec):
    """Reflect finds the round short and sends it back with feedback.

    `keep_results=True` carries the first round forward, so the second plan adds the
    remainder and the user is not shown the same candidates twice.
    """
    rec.plans = [
        Plan(goal="Generate 3.", done_criteria=["3 candidates"],
             steps=[ToolCall(tool="creative_thinker",
                             args={"thinking_mode": "generation", "width": 3})]),
        Plan(goal="Add one more.", done_criteria=["1 further candidate"],
             steps=[ToolCall(tool="creative_thinker",
                             args={"thinking_mode": "generation", "width": 1})]),
    ]
    rec.envelopes = [envelope(drafts=[candidate("One"), candidate("Two")]),
                     envelope(drafts=[candidate("Three")])]
    rec.judgments = [
        ReflectJudgment(route="replan", keep_results=True,
                        feedback="Two came back and the criterion asked for three."),
        ReflectJudgment(route="finish", answer="Three candidates in total."),
    ]

    state = turn(workspace, "give me 3 ideas", gaps=[GAP_ID])

    assert rec.chain() == ["plan.llm", "creative_thinker", "reflect.llm",
                           "plan.llm", "creative_thinker", "reflect.llm"]
    assert [i.title for i in state["ideas"]] == ["One", "Two", "Three"]
    assert "Two came back" in rec.to("plan.llm")[1].args["prompt"], "feedback reached the planner"


def test_replan_discarding_starts_the_round_clean(workspace, rec):
    """`keep_results=False` throws the round away rather than adding to it."""
    rec.plans = [
        Plan(goal="Generate.", done_criteria=["2 candidates"],
             steps=[ToolCall(tool="creative_thinker",
                             args={"thinking_mode": "generation", "width": 2})]),
        Plan(goal="Generate differently.", done_criteria=["2 candidates"],
             steps=[ToolCall(tool="creative_thinker",
                             args={"thinking_mode": "generation", "width": 2})]),
    ]
    rec.envelopes = [envelope(drafts=[candidate("Restates the gap")]),
                     envelope(drafts=[candidate("Names a mechanism")])]
    rec.judgments = [
        ReflectJudgment(route="replan", keep_results=False,
                        feedback="They restated the gap. Discarded."),
        ReflectJudgment(route="finish", answer="Here is one with a mechanism."),
    ]

    state = turn(workspace, "give me 2 ideas", gaps=[GAP_ID])

    assert [i.title for i in state["ideas"]] == ["Names a mechanism"]
    assert "Discarded" in rec.to("plan.llm")[1].args["prompt"]


def test_a_truncated_run_is_reported_as_truncated_not_as_a_shortfall(workspace, rec):
    """Hitting a compute ceiling is the normal way divergent thinking ends.

    The status reaches reflect so it can tell a budget-capped run apart from thinking that
    simply went badly — otherwise it replans into a budget that is already spent.
    """
    rec.plans = [Plan(goal="Generate 5.", done_criteria=["5 candidates"],
                      steps=[ToolCall(tool="creative_thinker",
                                      args={"thinking_mode": "generation", "width": 5})])]
    rec.envelopes = [envelope(drafts=[candidate("Only one")],
                              status="truncated", limit="max_llm_calls")]
    rec.judgments = [ReflectJudgment(route="finish",
                                     answer="One candidate — the run hit its budget.")]

    state = turn(workspace, "give me 5 ideas", gaps=[GAP_ID])

    result = state["results"][0]
    assert (result.status, result.limit) == ("truncated", "max_llm_calls")
    assert "truncated" in rec.to("reflect.llm")[0].args["prompt"].lower()


def test_the_reflect_loop_stops_at_the_iteration_cap(workspace, rec):
    """A reflect that keeps asking to replan is overridden, and its answer is what shows."""
    rec.plans = [Plan(goal="Generate.", done_criteria=["1 candidate"],
                      steps=[ToolCall(tool="creative_thinker",
                                      args={"thinking_mode": "generation", "width": 1})])
                 for _ in range(3)]
    rec.envelopes = [envelope(drafts=[candidate(f"Round {i}")]) for i in range(3)]
    rec.judgments = [ReflectJudgment(route="replan", keep_results=False, feedback="More.",
                                     answer=f"Round {i} was short.") for i in range(3)]

    state = turn(workspace, "give me ideas", gaps=[GAP_ID])

    assert len(rec.to("plan.llm")) == 3, "capped at MAX_ITERATIONS, not looping forever"
    assert state["answer"] == "Round 2 was short."


def test_accepting_a_draft_mints_an_id_and_carries_the_trace(workspace, rec):
    """The accept gate — the only path from a draft to the idea graph.

    A draft has no `idea_id` until this call; the thinking graph lives on the envelope's
    carrier record, so the accept step replays the real envelope to carve the branch that
    produced the accepted candidate onto it.
    """
    rec.plans = [Plan(goal="Generate 2.", done_criteria=["2 candidates"],
                      steps=[ToolCall(tool="creative_thinker",
                                      args={"thinking_mode": "generation", "width": 2})])]
    rec.envelopes = [envelope(drafts=[candidate("Taken", node="n0"),
                                  candidate("Left", node="n1")], nodes=2)]
    rec.judgments = [ReflectJudgment(route="finish", answer="Two candidates.")]

    turn(workspace, "give me 2 ideas", gaps=[GAP_ID])
    before = set(storage.list_idea_ids(CATEGORY))

    written = commit_selection(CATEGORY, "s1", "run_1", ["run_1#0"])

    assert len(written) == 1
    new_id = written[0]
    assert new_id not in before
    record = storage.read_idea_json(CATEGORY, new_id)
    assert record["title"] == "Taken"
    assert record["addresses"] == [GAP_ID]
    assert record["runs"], "the thinking that produced it came along"
    assert "Left" not in json.dumps(record), "the candidate that was not taken is not written"


def test_accepting_something_the_run_never_offered_writes_nothing(workspace, rec):
    """The rule that survives from the orchestrator's own commit path."""
    rec.plans = [Plan(goal="Generate.", done_criteria=["1 candidate"],
                      steps=[ToolCall(tool="creative_thinker",
                                      args={"thinking_mode": "generation", "width": 1})])]
    rec.envelopes = [envelope(drafts=[candidate("Offered")])]
    rec.judgments = [ReflectJudgment(route="finish", answer="One candidate.")]

    turn(workspace, "give me an idea", gaps=[GAP_ID])
    before = set(storage.list_idea_ids(CATEGORY))

    assert commit_selection(CATEGORY, "s1", "run_1", ["idea_never_offered"]) == []
    assert set(storage.list_idea_ids(CATEGORY)) == before


def test_a_pasted_note_and_a_dropped_document_reach_the_thinker(workspace, rec):
    """The user pasted a constraint and dropped a file into the session earlier.

    Neither is a workspace record, so neither can be loaded by id — they exist only in the
    session — and without reading them the turn would think about the gap while ignoring
    what the user actually put in front of it.

    The two are shown differently on purpose. The note reaches the planner in full, because
    "one GPU" is an instruction that changes what a good plan is. The document does not:
    its text goes to the thinker through the context object, while the planner is told only
    that it is there. A document pasted into a planning prompt buries the rules the planner
    is following.

    A PDF is a third case: nothing extracts its text yet, so it arrives saying so rather
    than arriving empty, and no step can mistake an unread document for an empty one.
    """
    drop(notes=["Only ideas that train on one GPU."],
         files=[("constraints.md", "Hardware budget\n" + "detail line\n" * 400),
                ("chen2024.pdf", "%PDF-1.7 binary bytes")])

    rec.plans = [Plan(goal="Generate within the user's constraint.",
                      done_criteria=["1 candidate"],
                      steps=[ToolCall(tool="creative_thinker", args={
                          "thinking_mode": "generation", "width": 1,
                          "instruction": "Respect the single-GPU constraint."})])]
    rec.envelopes = [envelope(drafts=[candidate("Sparse kernel attention")])]
    rec.judgments = [ReflectJudgment(route="finish", answer="One candidate, single-GPU.")]

    turn(workspace, "ideas for this gap", gaps=[GAP_ID])

    sent = rec.to("creative_thinker")[0].args
    assert sent["context_user_input"] == ["note_0", "constraints.md", "chen2024.pdf"]
    given = {d["id"]: d for d in sent["context"]["user_input"]}
    assert given["constraints.md"]["text"].count("detail line") == 400, (
        "the thinker gets the file, not the 400-char digest the UI stores")
    assert given["note_0"]["kind"] == "note"
    assert given["chen2024.pdf"]["extracted"] is False
    assert "cannot be read" in given["chen2024.pdf"]["text"], (
        "an unread document says so; it does not arrive as an empty one")

    prompt = rec.to("plan.llm")[0].args["prompt"]
    assert "Only ideas that train on one GPU." in prompt, "a note is steering"
    assert "constraints.md" in prompt, "the planner knows the document is in scope"
    assert "detail line" not in prompt, "but its body is not pasted into the plan prompt"
    assert "chen2024.pdf (its text cannot be read" in prompt


def test_a_second_turn_sees_the_first_turns_drafts_and_transcript(workspace, rec):
    """A session remembers, so a follow-up can refer to what just happened.

    The previous turn's drafts come back as objects in `context.ideas` — they were never
    committed, so there is no id to load them by — and the exchange comes back as history.
    """
    rec.plans = [
        Plan(goal="Generate 2.", done_criteria=["2 candidates"],
             steps=[ToolCall(tool="creative_thinker",
                             args={"thinking_mode": "generation", "width": 2})]),
        Plan(goal="Sharpen one.", done_criteria=["1 revised"],
             steps=[ToolCall(tool="creative_thinker",
                             args={"thinking_mode": "refinement",
                                   "instruction": "Make the second concrete."})]),
    ]
    made = [candidate("First candidate"), candidate("Second candidate")]
    rec.envelopes = [envelope(drafts=made), envelope(idea=made[1], answer="Done.")]
    rec.judgments = [ReflectJudgment(route="finish", answer="Two candidates."),
                     ReflectJudgment(route="finish", answer="Made the second concrete.")]

    turn(workspace, "give me 2 ideas", gaps=[GAP_ID], run_id="run_1")
    turn(workspace, "make the second one more concrete", run_id="run_2")

    prompt = rec.to("plan.llm")[1].args["prompt"]
    assert "give me 2 ideas" in prompt, "the earlier turn is in the transcript"
    assert "First candidate" in prompt and "Second candidate" in prompt, "drafts came back"

    sent = rec.to("creative_thinker")[1].args
    assert sent["mode"] == "refinement"
    assert {"First candidate", "Second candidate"} & {sent["subject_title"]}


def test_a_turn_that_has_spent_its_budget_stops_instead_of_replanning(workspace, rec):
    """Reflect asks for another round, and there is nothing left to run it with.

    Replanning here would spend a planning call to produce a step that can only truncate.
    The turn ends on the budget the way it ends on the iteration cap, and reflect's answer
    is what the user sees.
    """
    rec.plans = [Plan(goal="Generate.", done_criteria=["3 candidates"],
                      steps=[ToolCall(tool="creative_thinker",
                                      args={"thinking_mode": "generation", "width": 3})])]
    rec.envelopes = [envelope(drafts=[candidate("The only one")], llm_calls=0)]
    rec.judgments = [ReflectJudgment(route="replan", keep_results=True,
                                     feedback="One came back, three were asked for.",
                                     answer="One candidate — the turn ran out of budget.")]

    state = turn(workspace, "give me 3 ideas", gaps=[GAP_ID])

    assert len(rec.to("plan.llm")) == 1, "no second plan was made"
    assert state["spent"] >= MAX_LLM_CALLS_PER_TURN
    assert state["answer"] == "One candidate — the turn ran out of budget."


def test_the_turn_budget_is_divided_across_the_steps_it_plans(workspace, rec):
    """The thinker's ceiling is per call; a turn may hold several. The orchestrator slices.

    Without this a two-step plan over three reflect rounds can spend six times the ceiling
    anyone intended.
    """
    rec.plans = [Plan(goal="Generate then sharpen.", done_criteria=["2 candidates"], steps=[
        ToolCall(tool="creative_thinker", args={"thinking_mode": "generation", "width": 2}),
        ToolCall(tool="creative_thinker", needs=[1], args={"thinking_mode": "refinement"}),
    ])]
    made = [candidate("One"), candidate("Two")]
    rec.envelopes = [envelope(drafts=made, llm_calls=8), envelope(idea=made[0], llm_calls=5)]
    rec.judgments = [ReflectJudgment(route="finish", answer="Done.")]

    state = turn(workspace, "ideas then sharpen", gaps=[GAP_ID])

    first, second = [c.args["budget_llm_calls"] for c in rec.to("creative_thinker")]
    assert first == MAX_LLM_CALLS_PER_TURN // 2, "the first of two steps gets half the turn"
    # Recomputed as it goes rather than split up front: step 1 came in at 8 of its 60, so
    # step 2 is handed everything that is actually left rather than a stale half.
    assert second == MAX_LLM_CALLS_PER_TURN - 8
    assert state["spent"] == 13, "charged what they reported, not what they were allowed"
