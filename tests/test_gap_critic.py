from src.agents.paper_agent.subagents.gap.subagents.critic import agent as critic
from src.common.schemas.gap_verify import GapCritique


def test_critic_ignores_issues_without_a_valid_candidate_gap_id(monkeypatch):
    roles = []
    monkeypatch.setattr(critic, "get_llm", lambda role: roles.append(role) or object())
    monkeypatch.setattr(
        critic,
        "chat_with_structured_output",
        lambda *args: GapCritique.model_validate(
            {
                "status": "issues",
                "issues": [
                    {"gap_id": None, "severity": "major", "reason": "Malformed response."},
                    {"gap_id": "unknown", "severity": "major", "reason": "Unknown candidate."},
                    {"gap_id": "gap-1", "severity": "minor", "reason": "Needs wording changes."},
                ],
                "missing_hints": [],
            }
        ),
    )

    result = critic.run_critic(
        {"abstract": "Source text."},
        ["abstract"],
        [{"gap_id": "gap-1", "grounding_status": "matched"}],
    )

    assert result["status"] == "issues"
    assert [(issue["gap_id"], issue["severity"]) for issue in result["issues"]] == [
        ("gap-1", "minor"),
    ]
    assert roles == ["cheap"]
