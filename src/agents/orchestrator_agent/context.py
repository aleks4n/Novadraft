"""Context assembly — the session's focus ids turned into records, plus one hop.

Deterministic on purpose: the planner decides *what to do*, never *what to read*. An LLM
that fetches its own context can quietly widen or narrow scope; this cannot.

What goes in:
  - the gaps and ideas the user selected (looked up across every gap stage, since a
    selected gap may sit in to_rank/ or master/)
  - **one hop out from each**: a gap's source papers, and where a selected idea came from
    (the gaps in its `addresses`, the ideas in its `relations`, plus those gaps' papers).
    A gap on its own is a sentence or two — the paper it came from is what says which
    setting the idea has to work in, and an idea without its gap can't be meaningfully
    refined.
  - every paper the investigator fetched into this session, so the next turn sees what the
    last one found
  - the drafts the previous turn offered, and the last few turns of the exchange, so a
    follow-up can refer to what just happened

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
from src.common.schemas.idea import IdeaRecord
from src.common.schemas.idea_compat import normalize
from src.common.tools import paper_access
from src.common.utils import storage
from src.agents.orchestrator_agent import session
from src.agents.orchestrator_agent.schemas import FocusSelection
from src.agents.orchestrator_agent.state import OrchestratorState

_HISTORY_TURNS = 3  # earlier turns fed back; more costs plan accuracy for little gain

# --- loading ------------------------------------------------------------------


def _load_gap(category: str, gap_id: str) -> GapRecord | None:
    _stage, raw = storage.find_gap(category, gap_id)
    try:
        return GapRecord.model_validate(raw)
    except (ValidationError, TypeError):
        return None


def _load_idea(category: str, idea_id: str) -> IdeaRecord | None:
    """One idea from the workspace, normalized on the way in.

    Records written by the earlier pipeline predate the current format — `summary` /
    `method` / `related_gaps`, and no `runs[]` — so they are read through
    `idea_compat.normalize`, which maps them onto `IdeaRecord` without writing anything
    back. A record already in the current shape passes through unchanged.
    """
    raw = storage.read_idea_json(category, idea_id)
    if not raw:
        return None
    try:
        return normalize(raw)
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
    """LangGraph node: focus + session -> {"context": Context, "history": [...]}."""
    category = state["category_slug"]
    session_id = state["session_id"]
    focus: FocusSelection = state.get("focus") or FocusSelection()
    earlier = session.read_runs(category, session_id, limit=_HISTORY_TURNS,
                               before=state.get("run_id", ""))

    gaps: dict[str, GapRecord] = {}
    ideas: dict[str, IdeaRecord] = {}
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

    # The drafts the previous turn offered. They exist only in the session file — nothing
    # accepted them, so they cannot be loaded by id — and without this a follow-up like
    # "make the second one concrete" has no second one to work on. Only the most recent
    # run: older declined drafts would pile up, and accepted ones are loadable by id.
    if earlier:
        previous_run = earlier[-1]
        for index, raw in enumerate(previous_run.get("ideas") or []):
            try:
                draft = IdeaRecord.model_validate(raw)
            except (ValidationError, TypeError):
                continue
            # Keyed by position, not by `idea_id`: a draft has none until it is accepted,
            # so keying on it would collapse every draft of a run onto one entry.
            ideas.setdefault(
                draft.idea_id or f"{previous_run.get('run_id', 'run')}#{index}", draft)

    # 2. one hop: what a selected idea came from. `addresses` names the gaps it attacks;
    #    `relations` names its typed edges to other ideas (child-of, variant-of, ...).
    for idea in list(ideas.values()):
        for gap_id in idea.addresses:
            if gap_id not in gaps:
                parent = _load_gap(category, gap_id)
                if parent:
                    gaps[gap_id] = parent
        for relation in idea.relations:
            if relation.target not in ideas:
                related = _load_idea(category, relation.target)
                if related:
                    ideas[relation.target] = related

    # 3. one hop: every in-scope gap's source papers
    papers: dict[str, dict] = {}
    for gap in gaps.values():
        for evidence in gap.evidence:
            if evidence.paper_id in papers:
                continue
            brief = _paper_brief(category, evidence.paper_id)
            if brief:
                papers[evidence.paper_id] = brief

    for fetched in _session_papers(category, session_id):
        papers.setdefault(fetched.get("paper_id", f"session_{len(papers)}"), fetched)

    return {
        "context": Context(
            gaps=list(gaps.values()),
            ideas=list(ideas.values()),
            papers=list(papers.values()),
            missing_ids=missing,
        ),
        "history": [{"message": r.get("message", ""), "answer": r.get("answer", "")}
                    for r in earlier],
    }
