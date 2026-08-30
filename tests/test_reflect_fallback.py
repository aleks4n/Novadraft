import pytest

from src.agents.orchestrator_agent.schemas import Plan, ToolResult
from src.agents.orchestrator_agent.subagents.reflect import agent
from src.common.schemas.idea import IdeaRecord


def _state(result: ToolResult) -> dict:
    return {
        "plan": Plan(goal="Produce one candidate", done_criteria=["1 candidate returned"]),
        "results": [result],
        "message": "Refine this idea",
        "iteration": 0,
    }


def test_malformed_reflection_finishes_and_preserves_completed_candidate(monkeypatch):
    def malformed(*_args, **_kwargs):
        raise ValueError(
            "ReflectJudgment: model returned no parseable structured output in 3 attempts"
        )

    monkeypatch.setattr(agent, "chat_with_structured_output", malformed)
    result = ToolResult(
        tool="creative_thinker",
        ideas=[IdeaRecord(title="A completed candidate")],
    )

    update = agent.reflect(_state(result))

    assert update["judgment"].route == "finish"
    assert update["judgment"].keep_results is True
    assert "1 candidate" in update["judgment"].answer
    assert update["iteration"] == 1


def test_reflection_does_not_hide_unrelated_value_errors(monkeypatch):
    def broken(*_args, **_kwargs):
        raise ValueError("invalid application state")

    monkeypatch.setattr(agent, "chat_with_structured_output", broken)

    with pytest.raises(ValueError, match="invalid application state"):
        agent.reflect(_state(ToolResult(tool="creative_thinker")))
