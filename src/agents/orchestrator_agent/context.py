"""Context assembly — the session's focus ids turned into records, plus one hop.

Deterministic on purpose: the planner decides *what to do*, never *what to read*. An LLM
that fetches its own context can quietly widen or narrow scope; this cannot.

What goes in:
  - the gaps and ideas the user selected (looked up across every gap stage, since a
    selected gap may sit in to_rank/ or master/)
  - **one hop out from each**: a gap's source papers, and an idea's parents (the gap it
    addresses, plus that gap's papers). A gap on its own is a sentence or two — the paper
    it came from is what says which setting the idea has to work in, and an idea without
    its gap can't be meaningfully refined.
  - every paper the investigator fetched into this session, so the next turn sees what the
    last one found

Exactly one hop. Two would pull in most of the workspace and stop being focus.

What deliberately does not go in: the workspace's whole paper library, and any paper's
full text (~60k chars each — the summary is what's useful for ideation, and the
investigator can fetch the rest on purpose).

Records that fail validation are named in `missing_ids` rather than raising — a stale id,
or a file written by an earlier prototype, shouldn't kill a turn.

This module decides what is *loaded*. `render.context_view` decides what the planner and
reflect then see of it, which is deliberately less; the creative thinker gets the whole
`Context` object.
"""

from __future__ import annotations

import json

from pydantic import ValidationError

from src.common.schemas.context import Context
from src.common.schemas.gap import GapRecord
from src.common.schemas.idea import Idea
from src.common.tools import paper_access
from src.common.utils import storage
from src.agents.orchestrator_agent import session
from src.agents.orchestrator_agent.schemas import FocusSelection
from src.agents.orchestrator_agent.state import OrchestratorState

# --- loading ------------------------------------------------------------------


def _load_gap(category: str, gap_id: str) -> GapRecord | None:
    _stage, raw = storage.find_gap(category, gap_id)
    try:
        return GapRecord.model_validate(raw)
    except (ValidationError, TypeError):
        return None


def _load_idea(category: str, idea_id: str) -> Idea | None:
    try:
        return Idea.model_validate(storage.read_idea_json(category, idea_id))
    except (ValidationError, TypeError):
        return None


def _paper_brief(category: str, paper_id: str) -> dict | None:
    """A paper reduced to what ideation needs: what it did, not its full text."""
    record = paper_access.get_paper(category, paper_id)
    if record is None:
        return None
    metadata = record.metadata.model_dump(mode="json") if record.metadata else {}
    return {
        "paper_id": paper_id,
        "title": metadata.get("title", ""),
        "year": paper_access.get_year(category, paper_id),
        "keywords": paper_access.get_keywords(category, paper_id),
        # `summary` is a plain dict on PaperRecord, not a model. `content` (the full
        # text, ~60k chars) is deliberately left out.
        "summary": record.summary or None,
    }


def _session_papers(category: str, session_id: str) -> list[dict]:
    """Papers the investigator fetched during this session."""
    papers_dir = session.session_papers_dir(category, session_id)
    if not papers_dir.exists():
        return []
    out = []
    for path in sorted(papers_dir.glob("*.json")):
        try:
            out.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    return out


# --- the node -----------------------------------------------------------------


def assemble_context(state: OrchestratorState) -> dict:
    """LangGraph node: focus -> {"context": Context}."""
    category = state["category_slug"]
    focus: FocusSelection = state.get("focus") or FocusSelection()

    gaps: dict[str, GapRecord] = {}
    ideas: dict[str, Idea] = {}
    missing: list[str] = []

    # 1. what the user actually selected
    for gap_id in focus.gap_ids:
        gap = _load_gap(category, gap_id)
        if gap is None:
            missing.append(gap_id)
        else:
            gaps[gap_id] = gap

    for idea_id in focus.idea_ids:
        idea = _load_idea(category, idea_id)
        if idea is None:
            missing.append(idea_id)
        else:
            ideas[idea_id] = idea

    # 2. one hop: a selected idea's parents (the gap it addresses, or its parent idea)
    for idea in list(ideas.values()):
        for edge in idea.parents:
            if edge.parent_kind == "gap" and edge.parent_id not in gaps:
                parent = _load_gap(category, edge.parent_id)
                if parent:
                    gaps[edge.parent_id] = parent
            elif edge.parent_kind == "idea" and edge.parent_id not in ideas:
                parent_idea = _load_idea(category, edge.parent_id)
                if parent_idea:
                    ideas[edge.parent_id] = parent_idea

    # 3. one hop: every in-scope gap's source papers
    papers: dict[str, dict] = {}
    for gap in gaps.values():
        for evidence in gap.evidence:
            if evidence.paper_id in papers:
                continue
            brief = _paper_brief(category, evidence.paper_id)
            if brief:
                papers[evidence.paper_id] = brief

    for fetched in _session_papers(category, state["session_id"]):
        papers.setdefault(fetched.get("paper_id", f"session_{len(papers)}"), fetched)

    return {
        "context": Context(
            gaps=list(gaps.values()),
            ideas=list(ideas.values()),
            papers=list(papers.values()),
            missing_ids=missing,
        )
    }
