from src.agents.creative_thinker.schemas import AgentStyle, Branch, BranchSet, Candidate
from src.agents.creative_thinker.subagents.expand_branch import agent as expand_agent
from src.agents.creative_thinker.subagents.generate_candidate import agent as generate_agent
from src.agents.creative_thinker.subagents.apply_edits import agent as edits_agent
from src.agents.creative_thinker.schemas import IdeaEdit, ThinkerCall
from src.agents.creative_thinker import agent as thinker_agent


def test_candidate_generation_uses_the_style_model(monkeypatch):
    requested = []
    marker = object()

    def get_llm(role, temperature, reasoning_effort=None):
        requested.append((role, reasoning_effort))
        return marker

    monkeypatch.setattr(generate_agent, "get_llm", get_llm)
    monkeypatch.setattr(
        generate_agent,
        "chat_with_structured_output",
        lambda llm, *_args: Candidate(title="Candidate") if llm is marker else None,
    )

    result = generate_agent.generate_candidate(
        style=AgentStyle(name="skeptic", model="reasoner"), context_md="brief",
    )

    assert result.title == "Candidate"
    assert requested == [("reasoner", None)]


def test_fast_refinement_expansion_uses_low_effort(monkeypatch):
    requested = []
    marker = object()

    def get_llm(role, temperature, reasoning_effort=None):
        requested.append((role, reasoning_effort))
        return marker

    monkeypatch.setattr(expand_agent, "get_llm", get_llm)
    monkeypatch.setattr(
        expand_agent,
        "chat_with_structured_output",
        lambda llm, *_args: BranchSet(branches=[Branch(focus="One", text="One")])
        if llm is marker else None,
    )

    branches = expand_agent.expand_branch(
        style=AgentStyle(name="skeptic", model="cheap"), idea_md="idea",
        path_md="", open_questions=[], width=1,
    )

    assert len(branches) == 1
    assert requested == [("cheap", "low")]


def test_writeback_uses_the_style_model(monkeypatch):
    requested = []
    marker = object()

    def get_llm(role, temperature):
        requested.append(role)
        return marker

    monkeypatch.setattr(edits_agent, "get_llm", get_llm)
    monkeypatch.setattr(
        edits_agent,
        "chat_with_structured_output",
        lambda llm, *_args: IdeaEdit(answer="Written back") if llm is marker else None,
    )

    result = edits_agent.apply_edits(
        style=AgentStyle(name="skeptic", model="reasoner"), idea_md="idea",
        path_md="one branch",
    )

    assert result.answer == "Written back"
    assert requested == ["reasoner"]


def test_grounding_question_contains_candidate_task_and_loaded_paper():
    candidate = Candidate(
        title="Re-seeded CEM", one_liner="Carry the prior elite mean forward.",
        description="Warm-start the next planning horizon.",
    )
    state = {
        "spec": ThinkerCall(mode="generation", task="Address CEM dimensionality"),
        "context_md": "Paper arxiv_2603.19312: LeWorldModel uses CEM.",
    }

    question = thinker_agent._candidate_investigation_question(candidate, state)

    assert "Re-seeded CEM" in question
    assert "Carry the prior elite mean" in question
    assert "Address CEM dimensionality" in question
    assert "arxiv_2603.19312" in question
