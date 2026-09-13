"""Budget accounting — deterministic, outside the model calls.

The ledger decides when a run is over for cost reasons and names the ceiling that
tripped. The caller then returns the last completed round's survivors and labels
the run ``truncated``.
"""

from __future__ import annotations

import time

from src.agents.creative_thinker.schemas import Budget


class BudgetLedger:
    """Counts what a run spent and answers "may I do one more?".

    ``limit`` is the name of the first ceiling that was hit — it goes straight
    into the run outcome and the envelope, so a truncated run always says which
    limit stopped it.
    """

    def __init__(self, budget: Budget | None = None):
        self.budget = budget or Budget()
        self.llm_calls = 0
        self.investigations = 0
        self.tokens = 0
        self.limit: str | None = None
        self._t0 = time.time()

    # --- spending ---
    def spend_llm(self, tokens: int = 0) -> None:
        self.llm_calls += 1
        self.tokens += tokens

    def spend_investigation(self) -> None:
        self.investigations += 1

    # --- checks ---
    @property
    def elapsed_s(self) -> float:
        return time.time() - self._t0

    def can_spend_llm(self, calls: int = 1) -> bool:
        """True when ``calls`` more model calls fit under every ceiling. Records
        the tripped limit on the first refusal so the outcome can name it."""
        if self.budget.max_llm_calls and self.llm_calls + calls > self.budget.max_llm_calls:
            self.limit = self.limit or "max_llm_calls"
            return False
        if self.budget.max_seconds and self.elapsed_s >= self.budget.max_seconds:
            self.limit = self.limit or "max_seconds"
            return False
        return True

    def can_investigate(self) -> bool:
        if (self.budget.max_investigations
                and self.investigations >= self.budget.max_investigations):
            self.limit = self.limit or "max_investigations"
            return False
        return True

    def note_limit(self, name: str) -> None:
        """Record a non-cost ceiling (e.g. ``max_depth``) the caller enforced."""
        self.limit = self.limit or name
