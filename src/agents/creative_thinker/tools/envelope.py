"""Envelope assembly — the last deterministic step of every run.

Both modes end here with the same shape: the run entry is appended to the idea
copy, the metrics are computed from the trace, and the status is fixed. A run that
accepted nothing still returns a valid ``idea`` with its trace appended and an
empty ``drafts``; only ``status`` reports that the search was partial.
"""

from __future__ import annotations

from src.agents.creative_thinker.schemas import (
    Envelope,
    Metrics,
    OpenQuestion,
    ProposedChange,
    Status,
    SuggestedOperation,
)
from src.agents.creative_thinker.tools.budget import BudgetLedger
from src.agents.creative_thinker.tools.diversity import cluster_count, mean_pairwise_distance
from src.agents.creative_thinker.tools.trace import TraceBuilder, append_run
from src.common.schemas.idea import IdeaRecord, RunOutcome, RunSummary

# An agent that flags every ambiguity is worse than one that never does. A run now
# raises at most one proposal by construction — the divergence offer in
# ``agent.writeback`` — so this is a backstop rather than a thinning step: a caller
# (or a replayed older envelope) carrying several still hands the user one decision.
_ESCALATION_ORDER = {"create-variant": 0, "create-child": 1}


def rate_limit_escalations(ops: list[SuggestedOperation]) -> list[SuggestedOperation]:
    """Carry at most one split proposal per run; everything else passes through.

    ``create-variant`` (the mechanism changed) outranks ``create-child`` (the same
    mechanism, made specific). The ``group`` exemption is gone with the grouped
    offer it existed for: an undecided round now asks a *question* with the branches
    as options instead of proposing a variant of each.
    """
    splits = [o for o in ops if o.type in _ESCALATION_ORDER]
    kept = [o for o in ops if o.type not in _ESCALATION_ORDER]
    if splits:
        kept.append(sorted(splits, key=lambda o: _ESCALATION_ORDER[o.type])[0])
    return kept


def _proposal_line(change: ProposedChange) -> str:
    """A proposal as the one line the next run reads off the record."""
    target = (change.target or "the idea").strip()
    return f"{target}: {change.change.strip()}".strip(": ")


def build_envelope(*, idea: IdeaRecord, trace: TraceBuilder, ledger: BudgetLedger,
                   status: Status, reason: str, drafts: list[IdeaRecord] | None = None,
                   operations: list[SuggestedOperation] | None = None,
                   proposed_changes: list[ProposedChange] | None = None,
                   questions: list[OpenQuestion] | None = None,
                   summary: RunSummary | None = None, answer: str = "") -> Envelope:
    """Append the run to ``idea.runs`` and wrap everything into the envelope.

    ``summary`` and ``answer`` are written into the run entry *and* returned on the
    envelope. That duplication is deliberate: the envelope is what this call hands
    back, the entry is what the next call will read, and they must be the same words.
    """
    operations = rate_limit_escalations(list(operations or []))
    texts = trace.texts()
    accepted = [n.node_id for n in trace.nodes.values() if n.status == "accepted"]
    limit = ledger.limit if status == "truncated" else None
    escalated = next((o for o in operations
                      if o.type in ("create-child", "create-variant", "merge")), None)

    entry = trace.build(RunOutcome(
        accepted_node=accepted[0] if accepted else None,
        escalation=escalated.type if escalated else None,
        escalation_node=escalated.node_id if escalated else None,
        escalation_diagnosis=(escalated.diagnosis or escalated.rationale) if escalated else "",
        truncated=status == "truncated",
        status=status, reason=reason, limit=limit,
        accepted_nodes=accepted, summary=summary or RunSummary(), answer=answer,
        proposed_changes=[_proposal_line(c) for c in (proposed_changes or [])],
    ))
    idea = append_run(idea, entry)

    return Envelope(
        idea=idea,
        answer=answer,
        drafts=list(drafts or []),
        suggested_operations=operations,
        proposed_changes=list(proposed_changes or []),
        questions=list(questions or []),
        status=status,
        status_reason=reason,
        limit=limit,
        run_id=trace.run_id,
        metrics=Metrics(
            llm_calls=ledger.llm_calls,
            investigations=ledger.investigations,
            tokens=ledger.tokens,
            duration_s=round(ledger.elapsed_s, 3),
            nodes=len(trace.nodes),
            mean_pairwise_distance=mean_pairwise_distance(texts),
            cluster_count=cluster_count(texts),
        ),
    )
