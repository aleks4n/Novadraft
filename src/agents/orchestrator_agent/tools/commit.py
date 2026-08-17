"""The only writer of the idea graph.

Not a graph node: accepting an idea needs no LLM, no plan and no reflection, so it is a
plain function the API calls when the user clicks. Nothing an agent produced reaches
`ideas/` without passing through here, which is what makes "an idea node is created only
by an explicit user action" true mechanically rather than by convention.

Ideas are read back from the run that offered them, so what gets written is exactly what
the user saw. A parent edge naming a node that doesn't exist is dropped rather than
written, so the graph never contains a dangling edge.
"""

from __future__ import annotations

from src.common.schemas.idea import Idea, IdeaEdge
from src.common.utils import storage
from src.agents.orchestrator_agent import session


def _keep_real_parents(category_slug: str, idea: Idea) -> list[IdeaEdge]:
    """Drop edges whose target isn't in the workspace."""
    kept = []
    for edge in idea.parents:
        if edge.parent_kind == "gap":
            _stage, record = storage.find_gap(category_slug, edge.parent_id)
            exists = record is not None
        else:
            exists = storage.read_idea_json(category_slug, edge.parent_id) is not None
        if exists:
            kept.append(edge)
    return kept


def commit_selection(
    category_slug: str, session_id: str, run_id: str, accepted: list[str]
) -> list[str]:
    """Write the ideas the user accepted from `run_id`. Returns the ids actually written.

    `accepted` are idea_ids as shown in the chat — an idea carries its id from birth, so
    the UI can name exactly what the user ticked. Ids the run never offered are ignored.
    """
    offered = {idea.idea_id: idea for idea in session.read_ideas(category_slug, session_id, run_id)}
    now = storage.now_iso()

    written: list[str] = []
    for idea_id in accepted:
        idea = offered.get(idea_id)
        if idea is None:
            continue  # not something this run offered — never write it
        committed = idea.model_copy(
            update={
                "status": "committed",
                "parents": _keep_real_parents(category_slug, idea),
                "created_at": idea.created_at or now,
                "updated_at": now,
            }
        )
        storage.write_idea_json(category_slug, committed.model_dump(mode="json"))
        written.append(committed.idea_id)
    return written
