from __future__ import annotations

from src.common.schemas.gap import Gap
from src.common.utils.prompts import load_prompt
from src.llm.registry import get_llm
from src.agents.validator_agent.schemas import SearchQueries
from src.agents.validator_agent.state import ValidatorState

_SYSTEM = load_prompt(__file__)

def generate_queries(state: ValidatorState) -> dict:
    """LangGraph node: Gap -> {"search_queries": SearchQueries}."""
    gap: Gap = state["gap"]
    keywords = ", ".join(gap.source_keywords) or "(none provided — infer the field)"
    user_message = (
        f"**Research gap:** {gap.title}\n\n"
        f"**Gap description**:\n{gap.description}\n\n"
        f"**Canonical description**: {gap.canonical_description}\n\n"
        f"**Source-paper keywords (anchor pool)**: {keywords}"
    )
    llm = get_llm("cheap").with_structured_output(SearchQueries, method="function_calling")
    queries: SearchQueries = llm.invoke([("system", _SYSTEM), ("human", user_message)])
    return {"search_queries": queries}