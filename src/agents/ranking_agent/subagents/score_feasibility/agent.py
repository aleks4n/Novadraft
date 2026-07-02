"""score_feasibility (LLM, reasoner) — judges how tractable making progress on the gap is."""

from __future__ import annotations

from src.common.schemas.gap import GapRecord
from src.common.utils.prompts import load_prompt
from src.llm.registry import chat_with_structured_output, get_llm
from src.agents.ranking_agent.schemas import FeasibilityAssessment
from src.agents.ranking_agent.state import RankingState

_SYSTEM = load_prompt(__file__)


def score_feasibility(state: RankingState) -> dict:
    """LangGraph node: gap -> {"feasibility": FeasibilityAssessment} (one reasoner call).

    The prompt is assembled from the gap's own fields (title / description / canonical) plus
    the validator's verdict reasoning — no attribute is filled on the gap.
    """
    gap: GapRecord = state["gap"]
    context = (
        f"**Research gap:** {gap.title}\n\n"
        f"**Gap description**:\n{gap.description}\n\n"
        f"**Canonical description**: {gap.canonical_description}\n\n"
    )
    if gap.verdict is not None:
        context += f"\n\n# Validator's finding (status: {gap.verdict.status})\n{gap.verdict.reasoning}"
    assessment = chat_with_structured_output(get_llm("reasoner"), _SYSTEM, context, FeasibilityAssessment)
    return {"feasibility": assessment}
