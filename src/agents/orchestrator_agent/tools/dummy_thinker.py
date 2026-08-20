"""A stand-in creative thinker, until the real one lands.

Deterministic and LLM-free on purpose: a demo should be instant, repeatable, and free,
and every path the real thinker will exercise is exercised here — draft ids, parent
edges, lens attribution, a thought graph, and the offer → accept → commit cycle.

It produces *shaped* output, not good ideas. The text is assembled from the gap's own
title and description so you can see the wiring carry real content end to end.

Swap-out point: `tools/registry.py` calls `think()`. When the real agent is ready, point
that import at it and delete this file — the `CreativeThinkerCall` /
`CreativeThinkerResult` contract is already what it must implement.
"""

from __future__ import annotations

import uuid

from src.common.schemas.idea import Idea, IdeaEdge, IdeaOrigin
from src.common.schemas.thinker import CreativeThinkerCall, CreativeThinkerResult

# Matches the lens names in the UI mock, so the demo looks like the design.
_DEFAULT_LENSES = ["The Skeptic", "The Transferer", "The Optimizer",
                   "The Extremist", "The Theorist"]

_ANGLE = {
    "skeptic": ("Question whether the premise holds",
                "Attack the assumption the gap rests on rather than the gap itself."),
    "transferer": ("Borrow a mechanism from another field",
                   "Import a technique that solves the structurally similar problem elsewhere."),
    "optimizer": ("Push the existing approach further",
                  "Keep the current method and remove its dominant cost."),
    "extremist": ("Take the setup to its limit",
                  "Assume the constraint is gone, then claw back what breaks."),
    "theorist": ("Look for the guarantee",
                 "Find the conditions under which the missing property provably holds."),
}


def _angle_for(lens: str) -> tuple[str, str]:
    """Match a lens to an angle, tolerantly.

    The planner writes lens names freely — 'skeptic', 'The Skeptic', 'a skeptical
    reviewer'. Match on any known keyword; otherwise build an angle from the lens itself,
    so an unrecognised lens still yields a *distinct* draft rather than a duplicate of
    every other one. Producing five identical candidates would be worse than useless — it
    hides exactly the bug this stand-in exists to surface.
    """
    key = lens.strip().lower()
    for name, angle in _ANGLE.items():
        if name in key:
            return angle
    return (f"Approach it as {lens.strip()}",
            f"Work the gap from the perspective of {lens.strip()}.")


def _new_id() -> str:
    return f"idea_{uuid.uuid4().hex[:8]}"


def _thoughts(run_seed: str, kept: str, pruned: str) -> list[dict]:
    """A two-branch trace: one kept, one pruned. Shape only — the real thinker owns this."""
    root = f"t_{run_seed}_0"
    return [
        {"thought_id": root, "parent_id": None, "text": kept, "status": "kept"},
        {"thought_id": f"t_{run_seed}_1", "parent_id": root,
         "text": f"Followed: {kept.lower()}", "status": "kept"},
        {"thought_id": f"t_{run_seed}_2", "parent_id": root,
         "text": f"Dropped: {pruned.lower()}", "status": "pruned"},
    ]


def _generate(call: CreativeThinkerCall) -> CreativeThinkerResult:
    """One draft per lens, each anchored to the first gap in context."""
    gaps = call.context.gaps
    if not gaps:
        return CreativeThinkerResult(
            text="No gap in context — generation needs one to work from."
        )

    gap = gaps[0]
    # `width` decides how many candidates come back; lenses only supply the angles. Top up
    # from the defaults when the planner named fewer than it asked for, truncate when more.
    lenses = list(call.lenses)
    for default in _DEFAULT_LENSES:
        if len(lenses) >= call.guard.width:
            break
        if default not in lenses:
            lenses.append(default)
    lenses = lenses[: call.guard.width]
    ideas: list[Idea] = []

    for lens in lenses:
        angle, approach = _angle_for(lens)
        seed = uuid.uuid4().hex[:6]
        ideas.append(
            Idea(
                idea_id=_new_id(),
                title=f"{angle}: {gap.title}",
                short=angle,
                description=(
                    f"{approach} The gap states: {gap.description.strip()[:280]} "
                    f"This draft was produced by the {lens} lens against "
                    f"{gap.gap_id}."
                ),
                method=[
                    f"Characterise precisely what {gap.title.lower()} leaves unresolved.",
                    f"{approach}",
                    "Define the experiment that would show whether it worked.",
                ],
                parents=[IdeaEdge(parent_id=gap.gap_id, parent_kind="gap")],
                thought_graph=_thoughts(seed, angle, "the variant that collapsed into the baseline"),
                origin=IdeaOrigin(lens=lens),
            )
        )

    return CreativeThinkerResult(
        ideas=ideas,
        text=(f"Fanned out over {len(lenses)} lens(es) against {gap.gap_id}; "
              f"{len(ideas)} candidate(s) survived the batch."),
    )


def _refine(call: CreativeThinkerCall) -> CreativeThinkerResult:
    """Rewrite the one idea in context and append this run's thinking to it."""
    ideas = call.context.ideas
    if not ideas:
        return CreativeThinkerResult(
            text="No idea in context — refinement works on exactly one existing idea."
        )

    idea = ideas[0]
    seed = uuid.uuid4().hex[:6]
    note = call.instruction.strip() or "sharpened the mechanism"
    refined = idea.model_copy(
        update={
            "description": f"{idea.description.rstrip()} Refined: {note}",
            "method": idea.method + [f"Address the weakness named in refinement: {note}"],
            "thought_graph": idea.thought_graph
            + _thoughts(seed, f"Deepened: {note}", "the branch that changed the core mechanism"),
            "status": "draft",
        }
    )
    return CreativeThinkerResult(
        ideas=[refined],
        text=f"Refined {idea.idea_id} at depth {call.guard.depth}: {note}.",
        escalation=None,
    )


def think(call: CreativeThinkerCall) -> CreativeThinkerResult:
    """The contract the real creative thinker must implement."""
    return _refine(call) if call.thinking_mode == "refinement" else _generate(call)
