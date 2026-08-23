"""Pydantic schemas for the SummaryVerifyAgent (Stage 3b).

Two tracked-item lists, both following the same growing/resolved pattern
(see `common/utils/issue_tracking.py`):
- `issues` — a field that exists but is wrong, imprecise, or incomplete.
- `missing_hints` — something the source states that isn't reflected
  ANYWHERE in the summary yet.

For each, there's an LLM-facing shape (what the critic returns for a single
round: an `id` echoed back to confirm a previously-open item is still open,
or omitted for a new one) and a persisted shape (the LLM shape plus
`resolved`/`created_round`/`resolved_round` bookkeeping that `critic.py`
maintains across rounds — and across separate top-level `summarize()`
calls, since it's loaded from `paper.json::summary_verification` each time).

`SummaryVerification` is the combined record (both persisted lists + the
deterministic grounding check) written to `paper.json::summary_verification`.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Severity = Literal["minor", "major"]


class CritiqueIssueLLM(BaseModel):
    """One issue as the critic LLM emits it for a single round."""

    id: str | None = None
    field: str
    severity: Severity
    description: str
    evidence_section: str | None = None


class CritiqueIssue(BaseModel):
    """One issue as persisted in `summary_verification.issues` — grows across
    rounds and across separate top-level `summarize()` calls; never shrinks,
    only gets `resolved` flipped to `True`."""

    id: str
    field: str
    severity: Severity
    description: str
    evidence_section: str | None = None
    resolved: bool = False
    created_round: int = 1
    resolved_round: int | None = None


class MissingHintLLM(BaseModel):
    """One missing-coverage hint as the critic LLM emits it for a single
    round."""

    id: str | None = None
    hint: str


class MissingHint(BaseModel):
    """One missing-coverage hint as persisted in
    `summary_verification.missing_hints` — same growing/resolved pattern as
    `CritiqueIssue`."""

    id: str
    hint: str
    resolved: bool = False
    created_round: int = 1
    resolved_round: int | None = None


class SummaryCritique(BaseModel):
    """LLM critic output — judges, never rewrites."""

    status: Literal["pass", "issues"]
    issues: list[CritiqueIssueLLM] = Field(default_factory=list)
    missing_hints: list[MissingHintLLM] = Field(default_factory=list)

    @classmethod
    def prompt_schema(cls) -> str:
        return """\
Return JSON with this exact shape:

{
  "status": "pass" | "issues",
  "issues": [
    {
      "id": string | null,          // echo back the id of a previously-open issue you
                                     //   are confirming is STILL present; omit/null for
                                     //   an issue you are reporting for the first time
      "field": string,              // dotted path into the summary, e.g. "approach.assumptions"
      "severity": "minor" | "major",// major = factually wrong/unsupported or a missing
                                     //   key contribution; minor = stylistic, incomplete nuance
      "description": string,        // 1-2 sentences, specific enough to act on
      "evidence_section": string | null  // canonical section name this issue concerns
    }
  ],
  "missing_hints": [
    {
      "id": string | null,          // echo back the id of a previously-open hint you are
                                     //   confirming is STILL missing; omit/null if new
      "hint": string                // short pointer to the source content no field
                                     //   captures at all — a phrase or section name is enough
    }
  ]
}

Only list an issue/hint if it is still actually present/missing in the CURRENT summary
below. If a previously-open item (see "Open issues" / "Open missing items from earlier
rounds") has been fixed/added, simply leave it out — do not list it with any status.

`issues` and `missing_hints` answer different questions: an issue is about a field that
exists but is wrong, imprecise, or incomplete; a missing hint is about something the source
states that isn't reflected ANYWHERE in the summary yet — don't make the same gap both an
issue and a missing hint, pick whichever is the better fit.

If the summary is faithful and complete, return {"status": "pass", "issues": [],
"missing_hints": []}."""


class GroundingResult(BaseModel):
    checked: list[str] = Field(default_factory=list)
    unmatched: list[str] = Field(default_factory=list)


class SummaryVerification(BaseModel):
    """Combined record written to paper.json::summary_verification."""

    status: Literal["pass", "issues"]
    attempts: int
    regenerated: bool
    grounding: GroundingResult
    issues: list[CritiqueIssue] = Field(default_factory=list)
    missing_hints: list[MissingHint] = Field(default_factory=list)
    checked_at: str
