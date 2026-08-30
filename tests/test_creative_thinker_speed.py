from src.agents.creative_thinker.schemas import AgentStyle, Branch, BranchSet, Candidate
from src.agents.creative_thinker.subagents.expand_branch import agent as expand_agent
from src.agents.creative_thinker.subagents.generate_candidate import agent as generate_agent


def test_rough_candidate_uses_flash_with_low_reasoning(monkeypatch):
    requested = []
    marker = object()

    def get_llm(role, temperature, reasoning_effort=None):
        requested.append((role, reasoning_effort))
        return marker

    monkeypatch.setattr(generate_agent, "get_llm", get_llm)
    monkeypatch.setattr(
        generate_agent,
        "chat_with_structured_output",
        lambda llm, *_args: Candidate(title="Fast candidate") if llm is marker else None,
    )

    result = generate_agent.generate_candidate(
        style=AgentStyle(name="skeptic", model="reasoner"), context_md="brief",
    )

    assert result.title == "Fast candidate"
    assert requested == [("cheap", "low")]


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
