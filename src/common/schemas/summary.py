"""Pydantic schema for PaperSummary — the LLM output from the summarize subagent."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

PaperType = Literal["method", "empirical_study", "survey", "theory", "benchmark", "position"]


class SummaryProblem(BaseModel):
    statement: str
    motivation: str
    prior_limitations: list[str] = Field(default_factory=list)


class SummaryApproach(BaseModel):
    summary: str
    key_ideas: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)


class SummaryContribution(BaseModel):
    type: str
    description: str
    evidence_section: str


class SummaryDataset(BaseModel):
    name: str
    task: str
    role: str


class SummaryMetric(BaseModel):
    name: str
    value: str
    dataset: str
    comparison: str


class SummaryResults(BaseModel):
    headline: str
    caveats: list[str] = Field(default_factory=list)


# Per-field prompt snippets, shared between the full schema (PaperSummary) and
# the partial-revision schema (PaperSummaryPatch) so the two never drift apart.
_FIELD_SHAPES: dict[str, str] = {
    "paper_type": '"paper_type": "method" | "empirical_study" | "survey" | "theory" | '
                  '"benchmark" | "position",',
    "keywords": '"keywords": [string, ...],          // paper-specific terms, authors\' language',
    "topics": '"topics": [string, ...],            // higher-level KB tags (3-6)',
    "problem": """"problem": {
    "statement": string,
    "motivation": string,
    "prior_limitations": [string, ...]
  },""",
    "approach": """"approach": {
    "summary": string,                // 3-5 sentences
    "key_ideas": [string, ...],       // 2-4 items
    "assumptions": [string, ...]
  },""",
    "contributions": """"contributions": [
    {
      "type": string,                 // e.g. method, experimental, observational, theory,
                                       //   dataset, clinical, survey, framework, instrument,
                                       //   protocol, case_study, model — pick what fits the field
      "description": string,
      "evidence_section": string      // canonical section name
    }
  ],""",
    "datasets": """"datasets": [                       // also covers cohorts, specimen sets, measurement runs,
                                       //   corpora — any data the paper uses. May be empty.
    { "name": string, "task": string, "role": string }   // role: evaluation, training, pretraining,
                                                          //   primary_data, control, validation, etc.
  ],""",
    "metrics": """"metrics": [
    { "name": string, "value": string, "dataset": string,
      "comparison": string }          // e.g. SOTA, baseline, ablation, control, placebo,
                                       //   theoretical_prediction, prior_work, none
  ],""",
    "results": """"results": {
    "headline": string,
    "caveats": [string, ...]
  },""",
    "limitations_stated": '"limitations_stated": [string, ...],',
    "open_threads": '"open_threads": [string, ...],',
    "context_summary": '"context_summary": string            // 150-250 words, human-readable narrative',
}

# Declaration order for the full schema (and the canonical order patches are
# rendered in, regardless of which subset is requested).
_FIELD_ORDER = list(_FIELD_SHAPES.keys())


def _render_fields(fields: list[str]) -> str:
    """Join field snippets, stripping the trailing comma off the last one so
    a partial subset doesn't end with a dangling comma before the closing
    brace."""
    snippets = [_FIELD_SHAPES[f] for f in fields]
    if snippets:
        snippets[-1] = snippets[-1].rstrip()
        if snippets[-1].endswith(","):
            snippets[-1] = snippets[-1][:-1]
    return "\n  ".join(snippets)


class PaperSummary(BaseModel):
    paper_type: PaperType
    keywords: list[str]
    topics: list[str]
    problem: SummaryProblem
    approach: SummaryApproach
    contributions: list[SummaryContribution]
    datasets: list[SummaryDataset] = Field(default_factory=list)
    metrics: list[SummaryMetric] = Field(default_factory=list)
    results: SummaryResults
    limitations_stated: list[str] = Field(default_factory=list)
    open_threads: list[str] = Field(default_factory=list)
    context_summary: str

    @classmethod
    def prompt_schema(cls) -> str:
        body = _render_fields(_FIELD_ORDER)
        return "Return JSON with this exact shape:\n\n{\n  " + body + "\n}"


class PaperSummaryPatch(BaseModel):
    """Partial revision of a `PaperSummary`, for a feedback-driven extractor
    round. Only the top-level fields actually being revised are populated —
    every other field is left `None` and the caller (`extractor.run_extract`)
    merges just the populated keys into the existing `paper.json::summary`,
    so a field the critique never mentioned cannot drift."""

    paper_type: PaperType | None = None
    keywords: list[str] | None = None
    topics: list[str] | None = None
    problem: SummaryProblem | None = None
    approach: SummaryApproach | None = None
    contributions: list[SummaryContribution] | None = None
    datasets: list[SummaryDataset] | None = None
    metrics: list[SummaryMetric] | None = None
    results: SummaryResults | None = None
    limitations_stated: list[str] | None = None
    open_threads: list[str] | None = None
    context_summary: str | None = None

    @classmethod
    def prompt_schema(cls, fields: list[str]) -> str:
        """`fields` — top-level PaperSummary field names to include (unknown
        names are dropped). Order follows the canonical declaration order,
        not the order `fields` was passed in."""
        requested = [f for f in _FIELD_ORDER if f in fields]
        if not requested:
            requested = _FIELD_ORDER
        body = _render_fields(requested)
        return (
            "Return JSON containing ONLY these top-level keys — omit every "
            "other PaperSummary field entirely, do not set them to null:\n\n"
            "{\n  " + body + "\n}"
        )
