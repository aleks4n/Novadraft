"""The Investigator seam — the thinker's only outward call, and it is optional.

The Investigator (the convergent worker) is a separate component; here it is just
a callable ``(question: str) -> dict`` with ``{"answer", "sources", "found"}``.
The orchestrator injects it; nothing is imported from it, so the thinker stays
callable with no network and no workspace.

Two rules this enforces:

- ``tool_use`` off, no callable injected, or the ledger out of investigations →
  the question is **not** answered by guessing. It comes back as an
  ``investigate`` suggestion carrying the concrete question.
- ``tool_use`` on and the Investigator returns nothing → same outcome. An empty
  answer is a knowledge gap the thinker may not close, not licence to invent.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol

from src.agents.creative_thinker.tools.budget import BudgetLedger


class Investigator(Protocol):
    def __call__(self, question: str) -> dict: ...


@dataclass
class Finding:
    found: bool
    answer: str = ""
    sources: list[str] | None = None
    reason: str = ""  # why it was not answered, when found is False
    # Whether the Investigator was actually reached. ``found=False`` covers both "we
    # asked and got nothing" and "we never asked", and the trace must not report the
    # second as a call: a stream claiming `investigator called` on a run with
    # ``tool_use`` off is simply false, and it is the kind of false that is only
    # caught by someone reading the log very carefully.
    attempted: bool = False


def ask(question: str, *, investigator: Callable[[str], dict] | None,
        tool_use: bool, ledger: BudgetLedger) -> Finding:
    """Ask the Investigator, or explain why it was not asked. Never invents."""
    if not question:
        return Finding(found=False, reason="no question")
    if not tool_use:
        return Finding(found=False, reason="tool_use disabled")
    if investigator is None:
        return Finding(found=False, reason="no investigator wired")
    if not ledger.can_investigate():
        return Finding(found=False, reason="investigation budget exhausted")

    ledger.spend_investigation()
    try:
        result = investigator(question) or {}
    except Exception as exc:  # noqa: BLE001 — a failed lookup is an open question, not a crash
        return Finding(found=False, attempted=True,
                       reason=f"investigator error: {type(exc).__name__}: {exc}")

    answer = (result.get("answer") or "").strip()
    if not answer:
        return Finding(found=False, attempted=True, reason="investigator returned nothing")
    return Finding(found=True, attempted=True, answer=answer,
                   sources=list(result.get("sources") or []))
