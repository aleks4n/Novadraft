"""Pydantic schema for the gap subagent's critic pass (Stage 4b).

The critic only judges gap candidates an upstream extractor proposed; it never
invents or rewrites a gap.

Mirrors ``common/schemas/summary_verify.py``'s tracked-item shape: two
growing/resolved-tracked lists (``issues`` and ``missing_hints``), each with an
LLM-facing shape (``*LLM``, what the critic returns for a single round) and a
persisted shape (``id`` + ``resolved`` + round bookkeeping, maintained in
``gap_verification`` across rounds and across separate top-level
``extract_gaps()`` calls).
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Severity = Literal["minor", "major"]


class GapCritiqueIssueLLM(BaseModel):
    """One issue as the critic LLM emits it for a single round."""

    id: str | None = None
    # Providers occasionally emit a null gap_id despite the prompt. Keep that
    # one malformed item parseable; the critic discards it before persistence.
    gap_id: str | None = None
    severity: Severity
    reason: str


class GapCritiqueIssue(BaseModel):
    """One issue as persisted in `gap_verification.issues` — grows across
    rounds and across separate top-level `extract_gaps()` calls; never
    shrinks, only gets `resolved` flipped to `True`."""

    id: str
    gap_id: str
    severity: Severity
    reason: str
    resolved: bool = False
    created_round: int = 1
    resolved_round: int | None = None


class MissingHintLLM(BaseModel):
    """One missing-gap hint as the critic LLM emits it for a single round."""

    id: str | None = None
    hint: str


class MissingHint(BaseModel):
    """One missing-gap hint as persisted in `gap_verification.missing_hints`
    — same growing/resolved pattern as `GapCritiqueIssue`."""

    id: str
    hint: str
    resolved: bool = False
    created_round: int = 1
    resolved_round: int | None = None


class GapCritique(BaseModel):
    """LLM critic output — judges existing candidates, never rewrites."""

    status: Literal["pass", "issues"]
    issues: list[GapCritiqueIssueLLM] = Field(default_factory=list)
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
      "gap_id": string,             // must match one of the candidates' gap_id
      "severity": "minor" | "major",// major = not actually stated, or quote doesn't
                                     //   support it, or wrong claim-kind entirely;
                                     //   minor = debatable type/imprecise wording
      "reason": string              // 1-2 sentences, specific enough to act on
    }
  ],
  "missing_hints": [
    {
      "id": string | null,          // echo back the id of a previously-open hint you are
                                     //   confirming is STILL missing; omit/null if new
      "hint": string                // short pointer to a clearly stated gap no candidate
                                     //   covers — a phrase or section name is enough
    }
  ]
}

Only list an issue/hint if it is still actually present/missing in the candidates below. If
a previously-open item (see "Open issues" / "Open missing items from earlier rounds") has
been fixed/added, simply leave it out. If every candidate is sound and nothing looks
missing, return {"status": "pass", "issues": [], "missing_hints": []}."""
