"""Deep Read (LLM, reasoner) — Stage 5 of the validator.

Walks the ranked shortlist, downloads each paper's full text, and reads it against
the gap — one reasoner call and one PaperAssessment **per paper**. Takes the first
`target` papers whose source downloads; download failures are skipped and
backfilled by the next-ranked paper (their ids go to `skipped_ids`).

Each candidate is fetched once into a shared hidden cache category (`_validator_cache`)
via paper_fetch + paper_extract, then read from its paper.json; cache hits are reused
across gaps. Fetch/extract misses are skipped and backfilled by the next-ranked paper.
Reads are sequential for V1 — parallelizing the LLM calls is a later optimization.
"""

from __future__ import annotations

import json
import re
from typing import List, Optional

from src.common.schemas.gap import GapRecord
from src.common.schemas.paper import CandidatePaper
from src.common.schemas.verdict import PaperAssessment
from src.common.tools.paper_access import get_full_text
from src.common.tools.paper_extract import extract
from src.common.tools.paper_fetch import PaperAlreadyExistsError, PaperFetchError, fetch
from src.common.utils import storage
from src.common.utils.prompts import load_prompt
from src.llm.registry import chat_with_structured_output, get_llm
from src.agents.validator_agent.schemas import Assessment
from src.agents.validator_agent.state import ValidatorState

_SYSTEM = load_prompt(__file__)
_TARGET = 5  # how many papers to deep-read
_CHAR_BUDGET = 60_000  # cap full text fed to the prompt (head + tail kept)
_BLANKLINES_RE = re.compile(r"\n{3,}")
_CACHE = "_validator_cache"  # hidden workspace category caching fetched prior-art papers


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


def _analyse(llm, gap: GapRecord, paper: CandidatePaper, full_text: str) -> PaperAssessment:
    """One reasoner call on one paper's full text -> PaperAssessment (paper attached here)."""
    user_message = (
        f"**Research gap:** {gap.title}\n\n"
        f"**Gap description**:\n{gap.description}\n\n"
        f"**Canonical description**: {gap.canonical_description}\n\n"
        f"**Paper**: {paper.title}\n\n{_clip(full_text)}"
    )
    judgment: Assessment = chat_with_structured_output(llm, _SYSTEM, user_message, Assessment)
    return PaperAssessment(paper=paper, **judgment.model_dump())


def _ensure_cache() -> None:
    """Create the cache category's meta.json if missing — fetch's ensure_category needs it."""
    meta = storage.category_dir(_CACHE) / "meta.json"
    if meta.exists():
        return
    meta.parent.mkdir(parents=True, exist_ok=True)
    meta.write_text(
        json.dumps(
            {
                "category_slug": _CACHE,
                "category": "Validator prior-art cache",
                "description": "Internal cache of papers fetched during validation; not a research topic.",
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def _cache_fulltext(candidate: CandidatePaper) -> Optional[str]:
    """Fetch the candidate into the shared cache once, extract it, return its full text.

    Cache hits (already fetched for an earlier gap) are reused. None if the paper can't
    be confidently resolved, fetched, or extracted.
    """
    paper_id = candidate.paper_id
    try:
        result = fetch(_CACHE, candidate.url or paper_id.removeprefix("arxiv_"))
        if result.get("status") != "ok":
            return None  # ambiguous — no single confident match
        paper_id = result["paper"]["paper_id"]
    except PaperAlreadyExistsError as exc:
        paper_id = exc.paper_id  # cache hit — reuse what's already fetched
    except PaperFetchError:
        return None
    extract(_CACHE, paper_id)  # idempotent: skips if already extracted
    return get_full_text(_CACHE, paper_id)


def deep_read(state: ValidatorState) -> dict:
    """LangGraph node: shortlist -> {"assessments": [...], "skipped_ids": [...]}."""
    gap: GapRecord = state["gap"]
    shortlist: List[CandidatePaper] = state.get("shortlist", [])
    llm = get_llm("reasoner")
    _ensure_cache()

    assessments: List[PaperAssessment] = []
    skipped: List[str] = []
    for paper in shortlist:
        if len(assessments) >= _TARGET:
            break
        full_text = _cache_fulltext(paper)
        if not full_text:
            skipped.append(paper.paper_id)  # unfetchable / unextractable -> backfill
            continue
        assessments.append(_analyse(llm, gap, paper, full_text))
    return {"assessments": assessments, "skipped_ids": skipped}
