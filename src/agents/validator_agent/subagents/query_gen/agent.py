from __future__ import annotations

from src.common.schemas.gap import GapRecord
from src.common.tools.paper_access import get_keywords
from src.common.utils.prompts import load_prompt
from src.llm.registry import chat_with_structured_output, get_llm
from src.agents.validator_agent.schemas import SearchQueries
from src.agents.validator_agent.state import ValidatorState

_SYSTEM = load_prompt(__file__)


def _anchor_pool(gap: GapRecord, category_slug: str) -> str:
    """The gap's evidence papers' keywords/topics (deduped) — the query anchor."""
    terms: list[str] = []
    for ev in gap.evidence:
        for term in get_keywords(category_slug, ev.paper_id):
            if term not in terms:
                terms.append(term)
    return ", ".join(terms) or "(none available — infer the field)"


def generate_queries(state: ValidatorState) -> dict:
    """LangGraph node: gap -> {"search_queries": SearchQueries}."""
    gap: GapRecord = state["gap"]
    keywords = _anchor_pool(gap, state["category_slug"])
    user_message = (
        f"**Research gap:** {gap.title}\n\n"
        f"**Gap description**:\n{gap.description}\n\n"
        f"**Canonical description**: {gap.canonical_description}\n\n"
        f"**Source-paper keywords (anchor pool)**: {keywords}"
    )
    queries: SearchQueries = chat_with_structured_output(
        get_llm("cheap"), _SYSTEM, user_message, SearchQueries
    )
    return {"search_queries": queries}