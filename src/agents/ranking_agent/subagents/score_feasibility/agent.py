"""score_feasibility (LLM, reasoner) — judges how tractable each surviving gap is."""

from __future__ import annotations

from typing import Dict, Optional

from src.common.schemas.gap import Gap
from src.common.schemas.verdict import Verdict
from src.common.utils.prompts import load_prompt
from src.llm.registry import get_llm
from src.agents.ranking_agent.schemas import FeasibilityAssessment
from src.agents.ranking_agent.state import RankingState

_SYSTEM = load_prompt(__file__)


def _assess(llm, gap: Gap, verdict: Optional[Verdict]) -> FeasibilityAssessment:
    """One reasoner call -> FeasibilityAssessment for one gap."""
    context = (
        f"**Research gap:** {gap.title}\n\n"
        f"**Gap description**:\n{gap.description}\n\n"
        f"**Canonical description**: {gap.canonical_description}\n\n"
    )
    if verdict is not None:
        context += f"\n\n# Validator's finding (status: {verdict.status})\n{verdict.reasoning}"
    return llm.invoke([("system", _SYSTEM), ("human", context)])


def score_feasibility(state: RankingState) -> dict:
    """LangGraph node: impact survivors -> {"feasibility": {gap_id: FeasibilityAssessment}}."""
    config = state["config"]
    impact = state.get("impact", {})  # only gaps that passed openness + impact
    verdicts = state.get("verdicts", {})
    gaps_by_id = {g.gap_id: g for g in state.get("gaps", [])}
    llm = get_llm("reasoner").with_structured_output(FeasibilityAssessment, method="function_calling")

    feasibility: Dict[str, FeasibilityAssessment] = {}
    for gap_id in impact:
        gap = gaps_by_id.get(gap_id)
        if gap is None:
            continue
        assessment = _assess(llm, gap, verdicts.get(gap_id))
        if assessment.score >= config.min_feasibility:
            feasibility[gap_id] = assessment
    return {"feasibility": feasibility}
