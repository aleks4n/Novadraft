"""Deep Read (LLM, reasoner) — Stage 5 of the validator.

Walks the ranked shortlist, downloads each paper's full text, and reads it against
the gap — one reasoner call and one PaperAssessment **per paper**. Takes the first
`target` papers whose source resolves; fetch/extraction failures are skipped and
backfilled by the next-ranked paper (their ids go to `skipped_ids`), so one bad paper
never aborts the whole gap.

Each candidate is fetched once into a shared hidden cache category (`_validator_cache`)
via paper_fetch + paper_extract, then read from its paper.json; cache hits are reused
across gaps. Reads are sequential and use the fast `cheap` model (deepseek-chat) rather
than the reasoner — full-paper comprehension it handles well while keeping each gap's
latency down. Parallelizing the reads is a later optimization.
"""

from __future__ import annotations

import json
import re
import threading
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

# Per-paper locks: when parallel gaps shortlist the same candidate, the second caller
# waits for the first fetch+extract instead of duplicating it (it then gets the cache hit).
_paper_locks_guard = threading.Lock()
_paper_locks: dict[str, threading.Lock] = {}


def _paper_lock(paper_id: str) -> threading.Lock:
    with _paper_locks_guard:
        return _paper_locks.setdefault(paper_id, threading.Lock())


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
    """One LLM call on one paper's full text -> PaperAssessment (paper attached here)."""
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

    Cache hits (already fetched for an earlier gap) are reused. Returns None if the paper
    can't be confidently resolved, fetched, or extracted — the candidate is then skipped and
    backfilled by the next-ranked one, so a single unfetchable / unextractable paper (e.g. a
    PDF-only paper when marker-pdf isn't installed) never aborts the whole gap.
    """
    paper_id = candidate.paper_id
    with _paper_lock(paper_id):  # serialize concurrent callers on the same paper
        try:
            result = fetch(_CACHE, candidate.url or paper_id.removeprefix("arxiv_"))
            if result.get("status") != "ok":
                return None  # ambiguous — no single confident match
            paper_id = result["paper"]["paper_id"]
        except PaperAlreadyExistsError as exc:
            paper_id = exc.paper_id  # cache hit — reuse what's already fetched
        except PaperFetchError:
            return None
        try:
            extract(_CACHE, paper_id)  # idempotent: skips if already extracted
        except Exception:
            return None  # extraction failed (missing marker-pdf, corrupt source, ...) — skip + backfill
        return get_full_text(_CACHE, paper_id)


def deep_read(state: ValidatorState) -> dict:
    """LangGraph node: shortlist -> {"assessments": [...], "skipped_ids": [...]}.

    Walks the shortlist in rank order, reading each paper against the gap until `_TARGET`
    papers are assessed; fetch/extract misses are skipped and backfilled by the next-ranked
    paper. Reads are sequential (parallelizing them is a later optimization).
    """
    gap: GapRecord = state["gap"]
    shortlist: List[CandidatePaper] = state.get("shortlist", [])
    llm = get_llm("cheap")
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
