"""Screening subagent: from ~15 paper candidates, select up to 8 most likely to address the gap."""

from __future__ import annotations

from typing import List

from src.common.schemas.gap import Gap
from src.common.schemas.paper import Paper
from src.common.utils.prompts import load_prompt
from src.llm.registry import get_llm
from src.agents.validator_agent.schemas import Screening
from src.agents.validator_agent.state import ValidatorState

_MAX_SELECT = 8
_SYSTEM = load_prompt(__file__, max_select=_MAX_SELECT)


def _render_candidates(candidates: List[Paper]) -> str:
    """Number the candidates 1..N as `[n] title / abstract` blocks for the prompt."""
    return "\n\n".join(
        f"[{i}] {c.title}\n{c.abstract}" for i, c in enumerate(candidates, start=1)
    )


def screen(state: ValidatorState) -> dict:
    """LangGraph node: candidates -> {"shortlist": ordered Papers (<= MAX_SELECT)}."""
    gap: Gap = state["gap"]
    candidates: List[Paper] = state.get("candidates", [])
    if not candidates:
        return {"shortlist": []}

    user_message = (
        f"**Research gap:** {gap.title}\n\n"
        f"**Gap description**:\n{gap.description}\n\n"
        f"**Canonical description**: {gap.canonical_description}\n\n"
        f"**Candidate papers**\n\n{_render_candidates(candidates)}"
    )
    llm = get_llm("cheap").with_structured_output(Screening, method="function_calling")
    result: Screening = llm.invoke([("system", _SYSTEM), ("human", user_message)])

    # Map 1-based positions back to Papers: validate range, dedupe, cap.
    shortlist: List[Paper] = []
    seen: set[int] = set()
    for n in result.selected:
        if not (1 <= n <= len(candidates)) or n in seen:
            continue
        seen.add(n)
        shortlist.append(candidates[n - 1])
        if len(shortlist) >= _MAX_SELECT:
            break
    return {"shortlist": shortlist}
