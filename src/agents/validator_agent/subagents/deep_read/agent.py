"""Deep Read (LLM, reasoner) — Stage 5 of the validator.

Walks the ranked shortlist, downloads each paper's full text, and reads it against
the gap — one reasoner call and one PaperAssessment **per paper**. Takes the first
`target` papers whose source downloads; download failures are skipped and
backfilled by the next-ranked paper (their ids go to `skipped_ids`).

Fetch goes through common (interim downloader for now). Reads are sequential for
V1 — parallelizing the LLM calls is a later optimization.
"""

from __future__ import annotations

import re
from typing import List

from src.common.schemas.gap import Gap
from src.common.schemas.paper import Paper
from src.common.schemas.verdict import PaperAssessment
from src.common.tools.papers import fetch_fulltext
from src.common.utils.prompts import load_prompt
from src.llm.registry import get_llm
from src.agents.validator_agent.schemas import Assessment
from src.agents.validator_agent.state import ValidatorState

_SYSTEM = load_prompt(__file__)
_TARGET = 5  # how many papers to deep-read
_CHAR_BUDGET = 60_000  # cap full text fed to the prompt (head + tail kept)
_BLANKLINES_RE = re.compile(r"\n{3,}")


def _clip(text: str) -> str:
    """Collapse blank lines and cap to the char budget, keeping head (70%) + tail (30%).

    The tail preserves the conclusion / limitations — the richest source for
    `what_remains_open`.
    """
    text = _BLANKLINES_RE.sub("\n\n", text).strip()
    if len(text) <= _CHAR_BUDGET:
        return text
    head = int(_CHAR_BUDGET * 0.7)
    return f"{text[:head]}\n\n[... truncated to fit context ...]\n\n{text[-(_CHAR_BUDGET - head):]}"


def _analyse(llm, gap: Gap, paper: Paper, full_text: str) -> PaperAssessment:
    """One reasoner call on one paper's full text -> PaperAssessment (paper attached here)."""
    user_message = (
        f"**Research gap:** {gap.title}\n\n"
        f"**Gap description**:\n{gap.description}\n\n"
        f"**Canonical description**: {gap.canonical_description}\n\n"
        f"**Paper**: {paper.title}\n\n{_clip(full_text)}"
    )
    judgment: Assessment = llm.invoke([("system", _SYSTEM), ("human", user_message)])
    return PaperAssessment(paper=paper, **judgment.model_dump())


def deep_read(state: ValidatorState) -> dict:
    """LangGraph node: shortlist -> {"assessments": [...], "skipped_ids": [...]}."""
    gap: Gap = state["gap"]
    shortlist: List[Paper] = state.get("shortlist", [])
    llm = get_llm("reasoner").with_structured_output(Assessment, method="function_calling")

    assessments: List[PaperAssessment] = []
    skipped: List[str] = []
    for paper in shortlist:
        if len(assessments) >= _TARGET:
            break
        full_text = fetch_fulltext(paper.paper_id)
        if not full_text:
            skipped.append(paper.paper_id)  # no source -> backfill from next-ranked
            continue
        assessments.append(_analyse(llm, gap, paper, full_text))
    return {"assessments": assessments, "skipped_ids": skipped}
