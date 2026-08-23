"""Screening subagent: from ~15 paper candidates, select up to 8 most likely to address the gap."""

from __future__ import annotations

from typing import List

from src.common.schemas.gap import GapRecord
from src.common.schemas.paper import CandidatePaper
from src.common.utils.prompts import load_prompt
from src.llm.registry import chat_with_structured_output, get_llm
from src.agents.validator_agent.schemas import Screening
from src.agents.validator_agent.state import ValidatorState

_MAX_SELECT = 8
_SYSTEM = load_prompt(__file__, max_select=_MAX_SELECT)


def _render_candidates(candidates: List[CandidatePaper]) -> str:
    """Number the candidates 1..N as `[n] title / abstract` blocks for the prompt."""
    return "\n\n".join(
        f"[{i}] {c.title}\n{c.abstract}" for i, c in enumerate(candidates, start=1)
    )


def screen(state: ValidatorState) -> dict:
    """LangGraph node: candidates -> {"shortlist": ordered Papers (<= MAX_SELECT)}."""
    gap: GapRecord = state["gap"]
    candidates: List[CandidatePaper] = state.get("candidates", [])
    if not candidates:
        return {"shortlist": []}

    user_message = (
        f"**Research gap:** {gap.title}\n\n"
        f"**Gap description**:\n{gap.description}\n\n"
        f"**Canonical description**: {gap.canonical_description}\n\n"
        f"**Candidate papers**\n\n{_render_candidates(candidates)}"
    )
    result: Screening = chat_with_structured_output(
        get_llm("cheap"), _SYSTEM, user_message, Screening
    )

    # Map 1-based positions back to candidates: validate range, dedupe, cap.
    shortlist: List[CandidatePaper] = []
    seen: set[int] = set()
    for n in result.selected:
        if not (1 <= n <= len(candidates)) or n in seen:
            continue
        seen.add(n)
        shortlist.append(candidates[n - 1])
        if len(shortlist) >= _MAX_SELECT:
            break
    return {"shortlist": shortlist}
