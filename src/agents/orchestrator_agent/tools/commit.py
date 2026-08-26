"""The accept step — the only path from a draft to the idea graph.

Not a graph node: accepting needs no LLM, no plan and no reflection, so it is a plain
function the API calls when the user decides. Nothing an agent produced reaches `ideas/`
without passing through here, which is what makes "an idea node is created only by an
explicit user action" true mechanically rather than by convention.

The write itself is `idea_agent.apply_envelope`, which mints the `idea_id` at the moment
of acceptance, attaches the typed relation back to the parent, and records what was
*refused* on the parent's run outcome so the same branch is not proposed again. What this
module adds is the rule that survives from the orchestrator's own commit path: **only what
a named run actually offered may be written.** Ids are checked against that run's stored
result, so a request naming something the run never produced writes nothing.
"""

from __future__ import annotations

from src.agents.creative_thinker.schemas import Envelope
from src.agents.idea_agent import apply_envelope, new_idea_id
from src.agents.orchestrator_agent import session


def offer_handle(run_id: str, index: int, idea_id: str | None) -> str:
    """What one offered idea is addressed by: its `idea_id`, or its position in the run.

    A draft has no id until acceptance mints one, and the user still has to be able to
    point at it. Exported rather than inlined because whoever *shows* the offer and
    whoever passes the user's pick back here have to produce the same string — deriving
    it in two places is exactly how they stop matching, and a mismatch here writes
    nothing and says nothing.
    """
    return idea_id or f"{run_id}#{index}"


def commit_selection(
    category_slug: str, session_id: str, run_id: str, accepted: list[str]
) -> list[str]:
    """Write what the user accepted from `run_id`. Returns the ids actually written.

    `accepted` are the handles the chat showed — a committed idea's `idea_id`, or the
    positional handle a draft was offered under, since a draft has no id until this call
    mints one. Anything the run did not offer is ignored rather than written.

    The run's envelopes are replayed rather than rebuilt from the stored candidates. That
    matters: a generation run's thought graph lives on the envelope's carrier record and
    not on the candidates, so `apply_envelope` needs the real envelope to carve the right
    slice of it onto whichever draft is taken. Rebuilding one from the candidates alone
    would write an idea with no trace of how it was reached.
    """
    record = session.read_result(category_slug, session_id, run_id)
    offered = record.get("ideas") or []
    offers = record.get("offers") or []
    envelopes = record.get("envelopes") or []
    if not offered or len(offers) != len(offered):
        return []

    handles = {offer_handle(run_id, index, idea.get("idea_id")): index
               for index, idea in enumerate(offered)}
    picked = sorted({handles[key] for key in accepted if key in handles})
    if not picked:
        return []

    # Group by the envelope that produced each pick: one apply per envelope, so a run whose
    # plan had several thinker steps still writes each accepted idea against its own trace.
    by_envelope: dict[int, dict] = {}
    for index in picked:
        offer = offers[index]
        slot = by_envelope.setdefault(offer["envelope"], {"drafts": [], "carrier": False})
        if offer["draft"] is None:
            slot["carrier"] = True          # the refined record itself, not a new node
        else:
            slot["drafts"].append(offer["draft"])

    written: list[str] = []
    for envelope_index, slot in sorted(by_envelope.items()):
        raw = envelopes[envelope_index] if envelope_index < len(envelopes) else None
        if not raw:
            continue
        envelope = Envelope.model_validate(raw)
        if slot["carrier"] and not envelope.idea.idea_id:
            # A refinement whose subject was itself a draft from earlier in this turn:
            # the rewritten record has no id either, and `apply_envelope` persists only
            # what already has one — so accepting it would write nothing and say nothing.
            # Accepting an idea that has no id is a create; mint it here, where "the user
            # took this" is what is actually known.
            envelope.idea.idea_id = new_idea_id()
            if envelope.idea.state == "provisional":
                # Same rule `apply_envelope` applies to an accepted draft with no parent.
                envelope.idea.state = "root"
        result = apply_envelope(
            category_slug,
            envelope,
            accept_drafts=sorted(slot["drafts"]),
            # Only overwrite the source record when the user actually took the refined
            # version of it. A generation run's carrier is not a record anyone asked for.
            persist_idea=slot["carrier"],
        )
        written.extend(result.get("created") or [])
        written.extend(result.get("written") or [])
    return written
