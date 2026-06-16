"""Verdict (LLM, reasoner): Synthesizes the per-paper assessments into one Verdict for the gap,"""

from __future__ import annotations

from typing import List

from src.common.schemas.gap import Gap
from src.common.schemas.verdict import PaperAssessment, Verdict
from src.common.utils.prompts import load_prompt
from src.llm.registry import get_llm
from src.agents.validator_agent.schemas import VerdictJudgment
from src.agents.validator_agent.state import ValidatorState

_SYSTEM = load_prompt(__file__)


def _format_assessments(assessments: List[PaperAssessment]) -> str:
    """Render the assessments as numbered blocks — the LLM's input."""
    blocks = []
    for i, a in enumerate(assessments, start=1):
        snippets = "\n".join(f"    - {s!r}" for s in a.evidence_snippets) or "    (none)"
        blocks.append(
            f"## Paper {i}: {a.paper.title}\n"
            f"Relevance: {a.relevance_to_gap}\n"
            f"Contribution: {a.contribution_summary}\n"
            f"Still open: {a.what_remains_open}\n"
            f"Evidence:\n{snippets}"
        )
    return "\n\n".join(blocks)


def _metadata(state: ValidatorState, assessments: List[PaperAssessment]) -> dict:
    """Untyped debug bag the ranking agent ignores."""
    queries = state.get("search_queries")
    return {
        "queries_used": [q.query_type for q in queries.queries] if queries else [],
        "candidate_count": len(state.get("candidates", [])),
        "papers_deep_read": len(assessments),
        "skipped_ids": state.get("skipped_ids", []),
    }


def verdict(state: ValidatorState) -> dict:
    """LangGraph node: assessments -> {"verdict": Verdict}."""
    gap: Gap = state["gap"]
    assessments: List[PaperAssessment] = state.get("assessments", [])
    metadata = _metadata(state, assessments)

    if not assessments:
        # Nothing addressed the gap (or nothing downloaded) — no point spending a call.
        v = Verdict(
            gap_id=gap.gap_id,
            status="open",
            confidence=0.2,
            reasoning="No candidate papers were deep-read, so there is no evidence the gap has been addressed.",
            assessments=[],
            refined_gap=None,
            metadata=metadata,
        )
        return {"verdict": v}

    llm = get_llm("reasoner").with_structured_output(VerdictJudgment, method="function_calling")
    user_message = (
        f"# The gap\n{gap.description}\n\n"
        f"Canonical: {gap.canonical_description or gap.title}\n\n"
        f"# Assessments ({len(assessments)} papers deep-read)\n\n{_format_assessments(assessments)}"
    )
    judgment: VerdictJudgment = llm.invoke([("system", _SYSTEM), ("human", user_message)])

    v = Verdict(
        gap_id=gap.gap_id,
        status=judgment.status,
        confidence=judgment.confidence,
        reasoning=judgment.reasoning,
        assessments=assessments,
        refined_gap=None,  # gap refinement deferred (see SPEC.md)
        metadata=metadata,
    )
    return {"verdict": v}
