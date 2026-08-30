"""The chat surface driven by the orchestrator, through the real HTTP API.

The backend was built around one shape: a turn *is* one creative-thinker call, and what
comes back is one envelope, which is unpacked into the cards the user accepts from. A
planned turn does not fit that shape — it may think several times, or not at all — so
these tests drive the endpoints a browser drives and check the three places that had to
learn the difference:

  * `/send` starts a planned turn instead of a thinker call when asked to
  * `/land` reads the turn's own record, so a reply with no ideas is an ordinary turn
    rather than a failure, and cards come from every run rather than the first
  * accepting a card hands it back to the orchestrator's accept step, which is what
    carries the branch of reasoning behind it onto the record that gets written

The LLM steps and the subagents are recorded, as in `test_orchestrator_flows`; everything
between the HTTP request and them is real, including the background thread the run
executes on.
"""

from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from src.common.utils import storage
from src.agents.orchestrator_agent.schemas import Plan, ReflectJudgment, ToolCall

# The recorder, and a workspace with one gap and two committed ideas in it. Imported
# rather than rebuilt so there is one definition of what the subagents pretend to be.
from tests.test_orchestrator_flows import (  # noqa: F401 — fixtures used by name
    CATEGORY, GAP_ID, candidate, envelope, rec, workspace,
)


@pytest.fixture
def client(workspace):  # noqa: F811
    """The API, with its run manager pointed at the test workspace.

    `MANAGER` caches which runs it knows per slug, and a previous test's entries would
    otherwise be visible to this one — it is a module-level singleton, deliberately, so
    that a reload of the page finds the run still going.
    """
    from app.backend.main import app
    from app.backend.runs import MANAGER

    MANAGER._runs.clear()
    MANAGER._loaded.clear() if hasattr(MANAGER, "_loaded") else None
    return TestClient(app)


def _session(client) -> str:
    response = client.post(f"/api/topics/{CATEGORY}/sessions", json={"title": "t"})
    assert response.status_code == 200, response.text
    return response.json()["session_id"]


def _send(client, session_id: str, text: str, context=(), *, gate: bool = False) -> dict:
    """One turn. `gate` is the product default — the plan is offered and waits for Start.

    Off by default here so the scenarios below stay about what they are about (landing a
    reply, filing cards, accepting one); the gate has its own tests, which drive the same
    endpoints with it on.
    """
    response = client.post(
        f"/api/topics/{CATEGORY}/sessions/{session_id}/send",
        json={"text": text, "agent": "orchestrator", "context": list(context),
              "requirePlanApproval": gate})
    assert response.status_code == 200, response.text
    return response.json()


def _finished(client, run_id: str, timeout: float = 30.0) -> dict:
    """Wait for the background run thread, the way a polling client does."""
    from app.backend.runs import MANAGER

    deadline = time.time() + timeout
    while time.time() < deadline:
        meta = MANAGER.get(run_id)
        if meta and meta.get("status") != "running":
            return meta
        time.sleep(0.05)
    raise AssertionError(f"run {run_id} did not finish within {timeout}s")


def _land(client, session_id: str, run_id: str) -> dict:
    response = client.post(f"/api/topics/{CATEGORY}/sessions/{session_id}/land",
                           json={"runId": run_id})
    assert response.status_code == 200, response.text
    return response.json()


def test_a_conversational_turn_lands_as_a_reply_not_a_failure(client, rec):  # noqa: F811
    """"what can you do?" — the planner answers and calls nothing.

    The direct thinker path has no such turn: it always produces an envelope, so the
    absence of one means the run broke. A planned turn that answers without thinking is
    ordinary, and landing it as a failed message would put an error in the transcript
    where a perfectly good reply belongs.
    """
    rec.plans = [Plan(goal="Say what this does.", steps=[],
                      answer="I turn gaps into candidate research ideas.")]

    session_id = _session(client)
    started = _send(client, session_id, "what can you do?")
    _finished(client, started["run"]["run_id"])
    landed = _land(client, session_id, started["run"]["run_id"])

    assert landed["message"]["text"] == "I turn gaps into candidate research ideas."
    assert not landed["message"].get("failed")
    assert landed["drafts"] == [], "nothing was offered, so there is nothing to accept"


def test_a_planned_turn_offers_cards_and_accepting_one_writes_it(client, rec):  # noqa: F811
    """The whole path a user takes: select a gap, ask for ideas, accept one.

    The card carries the handle the turn offered it under, so accepting is handed back to
    the orchestrator rather than re-derived here — which is what brings the thinking that
    produced the idea along with it.
    """
    rec.plans = [Plan(goal="Generate 2.", done_criteria=["2 candidates"],
                      steps=[ToolCall(tool="creative_thinker",
                                      args={"thinking_mode": "generation", "width": 2})])]
    rec.envelopes = [envelope(drafts=[candidate("Kernel mixture"), candidate("Poly kernel")])]
    rec.judgments = [ReflectJudgment(route="finish", answer="I drafted two candidates.")]

    session_id = _session(client)
    started = _send(client, session_id, "give me 2 ideas",
                    context=[{"kind": "gap", "id": GAP_ID}])
    _finished(client, started["run"]["run_id"])
    landed = _land(client, session_id, started["run"]["run_id"])

    # The selection reached the planner as focus, from the session's loaded context.
    sent = rec.to("creative_thinker")[0].args
    assert sent["context_gaps"] == [GAP_ID]

    assert landed["message"]["text"] == "I drafted two candidates."
    titles = [d["_session"]["label"] for d in landed["drafts"]]
    assert titles == ["Kernel mixture", "Poly kernel"]
    manager_run = started["run"]["run_id"]
    assert all(d["_session"]["run_id"] == manager_run for d in landed["drafts"])
    assert all(d["_session"]["offer_run"] != manager_run for d in landed["drafts"])
    before = set(storage.list_idea_ids(CATEGORY))

    draft_id = landed["drafts"][0]["_session"]["draft_id"]
    accepted = client.post(
        f"/api/topics/{CATEGORY}/sessions/{session_id}/drafts/{draft_id}/accept")
    assert accepted.status_code == 200, accepted.text

    created = set(storage.list_idea_ids(CATEGORY)) - before
    assert len(created) == 1
    written = storage.read_idea_json(CATEGORY, created.pop())
    assert written["title"] == "Kernel mixture"
    assert written["runs"], "the thinking that produced it came along"
    assert "Poly kernel" not in json.dumps(written), "the card not taken was not written"


def test_landing_twice_does_not_duplicate_the_cards(client, rec):  # noqa: F811
    """A reload, or a second tab. Both halves of landing have to be idempotent.

    Keyed on the handle rather than on a position: a planned turn can think several times,
    so "the second idea of the envelope" does not identify a record across the whole turn.
    """
    rec.plans = [Plan(goal="Generate 1.", done_criteria=["1 candidate"],
                      steps=[ToolCall(tool="creative_thinker",
                                      args={"thinking_mode": "generation", "width": 1})])]
    rec.envelopes = [envelope(drafts=[candidate("Only one")])]
    rec.judgments = [ReflectJudgment(route="finish", answer="One candidate.")]

    session_id = _session(client)
    started = _send(client, session_id, "one idea", context=[{"kind": "gap", "id": GAP_ID}])
    _finished(client, started["run"]["run_id"])

    first = _land(client, session_id, started["run"]["run_id"])
    second = _land(client, session_id, started["run"]["run_id"])

    assert len(first["drafts"]) == 1
    assert len(second["drafts"]) == 1
    agent_messages = [m for m in second["session"]["messages"] if m["role"] == "agent"]
    assert len(agent_messages) == 1


# --- the plan gate ------------------------------------------------------------
# A planned turn is shown before it runs. These drive the two halves the browser drives:
# the turn that stops at its plan, and the Start that runs it with whatever the user
# changed about the arguments.


def _approve(client, session_id: str, run_id: str, plan: dict) -> dict:
    response = client.post(
        f"/api/topics/{CATEGORY}/sessions/{session_id}/plan/approve",
        json={"runId": run_id, "goal": plan["goal"], "steps": plan["steps"],
              "doneCriteria": plan["done_criteria"]})
    assert response.status_code == 200, response.text
    return response.json()


def test_a_gated_turn_offers_its_plan_and_runs_nothing(client, rec):  # noqa: F811
    """The plan comes back as the message; no subagent was called."""
    rec.plans = [Plan(goal="Generate 2.", done_criteria=["2 candidates"],
                      steps=[ToolCall(tool="creative_thinker", why="fan out",
                                      args={"thinking_mode": "generation", "width": 2})])]

    session_id = _session(client)
    started = _send(client, session_id, "give me 2 ideas", gate=True,
                    context=[{"kind": "gap", "id": GAP_ID}])
    _finished(client, started["run"]["run_id"])
    landed = _land(client, session_id, started["run"]["run_id"])

    message = landed["message"]
    assert message["plan_status"] == "proposed"
    assert message["plan"]["steps"][0]["tool"] == "creative_thinker"
    assert message["plan"]["steps"][0]["args"]["width"] == 2
    assert landed["drafts"] == [], "nothing ran, so there is nothing to accept"
    assert rec.to("creative_thinker") == [], "the subagent was never called"


def test_starting_an_edited_plan_runs_it_with_the_edited_arguments(client, rec):  # noqa: F811
    """The user raises the width and presses Start.

    The second run must not plan again — it would throw away the number they just chose —
    and the subagent must receive that number, not the planner's.
    """
    rec.plans = [Plan(goal="Generate 2.", done_criteria=["2 candidates"],
                      steps=[ToolCall(tool="creative_thinker",
                                      args={"thinking_mode": "generation", "width": 2})])]
    rec.envelopes = [envelope(drafts=[candidate("Kernel mixture"), candidate("Poly kernel"),
                                      candidate("Random features")])]
    rec.judgments = [ReflectJudgment(route="finish", answer="I drafted three candidates.")]

    session_id = _session(client)
    started = _send(client, session_id, "give me some ideas", gate=True,
                    context=[{"kind": "gap", "id": GAP_ID}])
    _finished(client, started["run"]["run_id"])
    proposed = _land(client, session_id, started["run"]["run_id"])["message"]["plan"]

    edited = json.loads(json.dumps(proposed))
    edited["steps"][0]["args"]["width"] = 3
    approved = _approve(client, session_id, started["run"]["run_id"], edited)
    assert approved["message"]["plan_status"] == "approved"

    _finished(client, approved["run"]["run_id"])
    landed = _land(client, session_id, approved["run"]["run_id"])

    assert len(rec.to("plan.llm")) == 1, "the approved plan was not re-planned"
    assert rec.to("creative_thinker")[0].args["width"] == 3
    assert landed["message"]["text"] == "I drafted three candidates."
    assert len(landed["drafts"]) == 3
    # The selection the plan was made against still reaches the run that executes it.
    assert rec.to("creative_thinker")[0].args["context_gaps"] == [GAP_ID]


def test_an_edit_the_tool_would_reject_is_refused_before_anything_runs(client, rec):  # noqa: F811
    """A width past the roster fails as a form error, not as a step mid-run."""
    rec.plans = [Plan(goal="Generate 2.", done_criteria=["2 candidates"],
                      steps=[ToolCall(tool="creative_thinker",
                                      args={"thinking_mode": "generation", "width": 2})])]

    session_id = _session(client)
    started = _send(client, session_id, "ideas", gate=True)
    _finished(client, started["run"]["run_id"])
    plan = _land(client, session_id, started["run"]["run_id"])["message"]["plan"]

    plan["steps"][0]["args"]["width"] = 99
    refused = client.post(
        f"/api/topics/{CATEGORY}/sessions/{session_id}/plan/approve",
        json={"runId": started["run"]["run_id"], "goal": plan["goal"],
              "steps": plan["steps"], "doneCriteria": plan["done_criteria"]})
    assert refused.status_code == 400, refused.text
    assert rec.to("creative_thinker") == []


def test_a_conversational_turn_is_never_gated(client, rec):  # noqa: F811
    """Nothing to approve: the planner answered, so the reply lands straight away."""
    rec.plans = [Plan(goal="Say what this does.", steps=[],
                      answer="I turn gaps into candidate research ideas.")]

    session_id = _session(client)
    started = _send(client, session_id, "what can you do?", gate=True)
    _finished(client, started["run"]["run_id"])
    landed = _land(client, session_id, started["run"]["run_id"])

    assert landed["message"].get("plan") is None
    assert landed["message"]["text"] == "I turn gaps into candidate research ideas."


def test_a_discarded_plan_stays_discarded(client, rec):  # noqa: F811
    rec.plans = [Plan(goal="Generate 1.", done_criteria=["1 candidate"],
                      steps=[ToolCall(tool="creative_thinker", args={"width": 1})])]

    session_id = _session(client)
    started = _send(client, session_id, "one idea", gate=True)
    _finished(client, started["run"]["run_id"])
    plan = _land(client, session_id, started["run"]["run_id"])["message"]["plan"]

    cancelled = client.post(f"/api/topics/{CATEGORY}/sessions/{session_id}/plan/cancel",
                            json={"runId": started["run"]["run_id"]})
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["message"]["plan_status"] == "cancelled"

    # And it cannot then be started behind the user's back.
    refused = client.post(
        f"/api/topics/{CATEGORY}/sessions/{session_id}/plan/approve",
        json={"runId": started["run"]["run_id"], "goal": plan["goal"],
              "steps": plan["steps"], "doneCriteria": plan["done_criteria"]})
    assert refused.status_code == 409, refused.text
    assert rec.to("creative_thinker") == []


def test_the_thinker_path_still_works_untouched(client, rec, monkeypatch):  # noqa: F811
    """The direct thinker path remains available when explicitly requested.

    The bridge is additive on purpose — the existing surface has to keep working while
    the frontend is still built around it.
    """
    from app.backend.runs import MANAGER

    # This scenario checks routing and input compatibility, not the thinker's graph
    # (covered by its own suite). Finish the detached worker immediately so the test
    # never starts real LLM work after its temporary workspace is torn down.
    monkeypatch.setattr(MANAGER, "_execute",
                        lambda run_id: MANAGER._update(run_id, status="done"))
    session_id = _session(client)
    response = client.post(f"/api/topics/{CATEGORY}/sessions/{session_id}/send",
                           json={"text": "ideas please", "agent": "creative_thinker", "width": 2})
    assert response.status_code == 200, response.text

    meta = MANAGER.get(response.json()["run"]["run_id"])
    assert meta["agent"] == "creative_thinker"
    assert meta["inputs"]["width"] == 2, "the composer's knobs still reach the thinker"
    _finished(client, response.json()["run"]["run_id"])
