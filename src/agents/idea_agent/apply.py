"""Apply a creative-thinker envelope to the workspace.

The creative thinker is a pure worker: it returns a modified ``idea.json`` copy and
proposals, and writes nothing. This module is the persistence step, field by
field::

    idea                  → persist the modified copy over the workspace record, or discard
    drafts + suggestions  → node/edge creation only after an explicit accept
    questions             → handed back to the caller, batched (never written)
    status                → returned for the reflector; a partial run is never treated
                            as a finished one

Two invariants:

- Nothing is written without a decision. ``accept`` is an explicit list of indices;
  an empty list persists the idea and creates nothing else.
- A rejection is recorded rather than dropped — it goes into the run's outcome on
  the parent record, so the same branch is not proposed again next session.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Iterable

from src.agents.creative_thinker.schemas import Envelope, SuggestedOperation
from src.common.schemas.idea import IdeaRecord, IdeaRelation, InheritedFrom
from src.common.utils import storage

_RELATION_FOR = {"create-child": "child-of", "create-variant": "variant-of",
                 "merge": "merged-into"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def new_idea_id() -> str:
    return f"idea_{uuid.uuid4().hex[:8]}"


def apply_envelope(category_slug: str, envelope: Envelope, *,
                   accept_drafts: Iterable[int] = (),
                   accept_operations: Iterable[int] = (),
                   persist_idea: bool = True) -> dict[str, Any]:
    """Write what the user accepted; return what happened and what still needs them.

    ``accept_drafts`` / ``accept_operations`` are indices into the envelope's lists —
    the explicit decision. Everything not named is *recorded as rejected*, never
    silently dropped.

    Returns ``{"idea_id", "written", "created", "rejected", "questions", "status"}``.
    """
    accepted_drafts = set(accept_drafts)
    accepted_ops = set(accept_operations)
    written: list[str] = []
    created: list[str] = []

    idea = envelope.idea.model_copy(deep=True)
    idea.updated_at = _now()
    idea.created_at = idea.created_at or idea.updated_at

    # --- drafts the user accepted become idea nodes -------------------------
    for index in sorted(accepted_drafts):
        if index >= len(envelope.drafts):
            continue
        draft = envelope.drafts[index].model_copy(deep=True)
        draft.idea_id = draft.idea_id or new_idea_id()
        draft.created_at = draft.updated_at = _now()
        draft.state = "derived" if idea.idea_id else "root"
        _attach_split(idea, draft, envelope)
        _carve_subgraph(idea, draft)
        storage.write_idea_json(category_slug, draft.model_dump(mode="json"))
        created.append(draft.idea_id)

    # --- record what was refused, on the run that proposed it ---------------
    rejected = _record_rejections(idea, envelope, accepted_ops, accepted_drafts)

    if persist_idea and idea.idea_id:
        storage.write_idea_json(category_slug, idea.model_dump(mode="json"))
        written.append(idea.idea_id)

    return {
        "idea_id": idea.idea_id,
        "written": written,
        "created": created,
        "rejected": rejected,
        # Surfaced to the user as ONE message, not one per branch — the orchestrator's
        # job, so they are handed back rather than written anywhere.
        "questions": [q.model_dump() for q in envelope.questions],
        "status": envelope.status,
    }


def promote_variant(category_slug: str, record: dict[str, Any], *, parent_idea_id: str,
                    rationale: str = "", run_id: str = "") -> dict[str, Any]:
    """Land a refinement's rewritten copy as its **own** idea instead of over its parent.

    The other half of the one question a refinement run can ask: it did what it was
    told and the result is arguably no longer the same idea, so the user chose to keep
    both. The rewrite gets a fresh id and a ``variant-of`` edge back to the record it
    came from; that record is not touched, because the copy already carries the run in
    its own ``runs[]`` — the thinking travels with the idea it produced.

    No sub-graph is carved (:func:`_carve_subgraph`), because nothing was carved out
    of anything: this record *is* the run's result, whole.
    """
    variant = IdeaRecord.model_validate(record).model_copy(deep=True)
    variant.idea_id = new_idea_id()
    variant.created_at = variant.updated_at = _now()
    variant.state = "derived"
    # Spine-level provenance, the same fact the edge states from the other side:
    # this content was copied from that record, up to the run before the one that
    # split it off. The relation is the graph; this is what a reader of the file sees.
    prior = [r.run_id for r in variant.runs if r.run_id and r.run_id != run_id]
    variant.inherited_from = InheritedFrom(idea_id=parent_idea_id,
                                           upto_run_id=prior[-1] if prior else None)
    variant.relations = list(variant.relations) + [IdeaRelation(
        type="variant-of", target=parent_idea_id,
        rationale=rationale or "kept as a variant of the idea it was refined from",
        run=run_id or None,
    )]
    storage.write_idea_json(category_slug, variant.model_dump(mode="json"))
    return {"idea_id": variant.idea_id, "written": [], "created": [variant.idea_id],
            "rejected": [], "questions": [], "status": "completed"}


def _attach_split(parent: IdeaRecord, draft: IdeaRecord, envelope: Envelope) -> None:
    """Write the typed edge for an accepted child or variant, with rationale and run id.

    Two ways a draft gets here and both need the edge. A **refinement** split arrives
    with the ``suggested_operation`` that proposed it, which carries the rationale. A
    **generation** run seeded from a parent produces children or variants directly, with
    no proposal in between — there the intent is recorded on the draft's own
    ``origin.suggested``. Falling back to it is what stops a generated variant from
    landing in the graph as an orphan with no edge back to what it varies.
    """
    if not parent.idea_id or draft.origin is None:
        return
    # Only a *split* proposal counts. Matching on node_id alone let an `investigate`
    # raised on the same branch shadow the fallback below, and the variant then landed
    # in the graph with no edge at all.
    op = next((o for o in envelope.suggested_operations
               if o.node_id == draft.origin.node_id and o.type in _RELATION_FOR), None)
    kind = op.type if op is not None else draft.origin.suggested
    relation = _RELATION_FOR.get(str(kind or ""))
    if relation is None:
        return
    draft.relations = list(draft.relations) + [IdeaRelation(
        type=relation, target=parent.idea_id,
        rationale=(op.rationale if op is not None
                   else f"generated as a {relation.replace('-of', '')} of {parent.title}"),
        run=envelope.run_id,
    )]


def _carve_subgraph(parent: IdeaRecord, draft: IdeaRecord) -> None:
    """Give the new idea its own copy of the branch that produced it.

    "Every node in the idea graph must be readable on its own": a promoted idea whose
    only history lives in its parent's file fails that. So the branch's path — the nodes
    along `origin.node_path` and the edges between them — is copied into the new record's
    `runs[]`, under the same run header. The parent keeps its full trace; this is a copy,
    not a move, because the parent's graph is evidence of what the run considered.

    **Appended, not assigned.** The draft already arrives holding the parent's inherited
    runs (``creative_thinker.agent._inherit``); the carved branch is the newest entry on
    top of that history, so the child's graph reads continuously from the parent's first
    run to the branch that split it off.
    """
    origin = draft.origin
    if origin is None or not origin.run_id:
        return
    if any(r.run_id == origin.run_id for r in draft.runs):
        return  # already carried in — never append the same run twice
    source = next((r for r in parent.runs if r.run_id == origin.run_id), None)
    if source is None:
        return

    path = origin.node_path or [p for p in (origin.node_id,) if p]
    keep = {node_id for node_id in path}
    nodes = [n.model_copy(deep=True) for n in source.nodes if n.node_id in keep]
    if not nodes:
        return
    for node in nodes:
        if node.node_id == origin.node_id:
            node.status = "accepted"

    entry = source.model_copy(deep=True, update={
        "nodes": nodes,
        "edges": [e.model_copy(deep=True) for e in source.edges
                  if e.from_node in keep and e.to_node in keep],
    })
    entry.outcome = entry.outcome.model_copy(update={
        "accepted_node": origin.node_id,
        "accepted_nodes": [origin.node_id] if origin.node_id else [],
        "summary": entry.outcome.summary.model_copy(update={
            "accepted": (f"carved from {origin.run_id} on acceptance: "
                         f"{entry.outcome.summary.accepted}").strip(": "),
        }),
    })
    draft.runs = list(draft.runs) + [entry]


def _record_rejections(idea: IdeaRecord, envelope: Envelope,
                       accepted_ops: set[int], accepted_drafts: set[int]) -> list[str]:
    """Mark refused proposals on the run entry that raised them.

    The trace is append-only, so this touches only the run this envelope just added —
    never an earlier one — and it is what makes "we already looked at that" answerable
    next session.
    """
    if not idea.runs:
        return []
    entry = idea.runs[-1]
    by_id = {n.node_id: n for n in entry.nodes}
    rejected: list[str] = []

    refused: list[SuggestedOperation] = [
        op for i, op in enumerate(envelope.suggested_operations)
        if i not in accepted_ops and op.type in _RELATION_FOR
    ]
    for op in refused:
        node = by_id.get(op.node_id)
        if node is not None and node.status != "accepted":
            node.status = "rejected"
            node.comparison = (node.comparison + " | " if node.comparison else "") + \
                f"user rejected {op.type}"
        rejected.append(op.node_id or op.type)

    for index, draft in enumerate(envelope.drafts):
        if index in accepted_drafts or not draft.origin:
            continue
        node = by_id.get(draft.origin.node_id or "")
        if node is not None and node.status not in ("accepted", "rejected"):
            node.status = "rejected"
            node.comparison = (node.comparison + " | " if node.comparison else "") + \
                "not accepted by the user"
            rejected.append(node.node_id)
    return rejected
