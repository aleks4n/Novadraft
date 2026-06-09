"""Prompt construction for the PaperSummaryAgent.

We feed only a curated subset of sections (see PAPER_AND_GAP_AGENT.md Stage 3)
and ask for a single JSON object matching the documented schema.
"""
from __future__ import annotations

# Canonical sections in priority order. `results` is truncated to first paragraph.
PRIORITY_SECTIONS = [
    "abstract",
    "introduction",
    "method",
    "results",
    "discussion",
    "limitations",
    "conclusion",
    "future_work",
]

# Rough char budget per section to keep total prompt bounded.
PER_SECTION_CHARS = {
    "abstract": 4000,
    "introduction": 6000,
    "method": 8000,
    "results": 1500,        # first paragraph only
    "discussion": 4000,
    "limitations": 4000,
    "conclusion": 3000,
    "future_work": 3000,
}


SYSTEM_PROMPT = """You are a research-paper summarization agent for a long-running \
research-ideation workspace. Your output is consumed by downstream agents \
(a GapAgent that hunts research gaps, and an IdeaAgent that proposes new \
research directions), so structure and factual fidelity matter more than prose.

Rules:
- Output a single JSON object matching the requested schema exactly. No prose \
outside the JSON.
- Be FACTUAL: only state what the paper itself says or directly implies. Do \
not speculate or add outside knowledge.
- Separate `assumptions` (what the paper takes for granted) from \
`limitations_stated` (what the authors explicitly acknowledge as weak).
- For theory/survey/position papers, `datasets` and `metrics` may be empty arrays.
- The paper may come from any field (CS, physics, biology, medicine, psychology, \
chemistry, social science, etc.). Use field-appropriate vocabulary in free-string \
fields — do not force machine-learning terminology onto non-ML papers.
- Keep `context_summary` between 150 and 250 words.
- Keep individual list items concise (one sentence each)."""


SCHEMA_INSTRUCTIONS = """Return JSON with this exact shape:

{
  "paper_type": "method" | "empirical_study" | "survey" | "theory" | "benchmark" | "position",
  "keywords": [string, ...],          // paper-specific terms, authors' language
  "topics": [string, ...],            // higher-level KB tags (3-6)

  "problem": {
    "statement": string,
    "motivation": string,
    "prior_limitations": [string, ...]
  },

  "approach": {
    "summary": string,                // 3-5 sentences
    "key_ideas": [string, ...],       // 2-4 items
    "assumptions": [string, ...]
  },

  "contributions": [
    {
      "type": string,                 // e.g. method, experimental, observational, theory,
                                       //   dataset, clinical, survey, framework, instrument,
                                       //   protocol, case_study, model — pick what fits the field
      "description": string,
      "evidence_section": string      // canonical section name
    }
  ],

  "datasets": [                       // also covers cohorts, specimen sets, measurement runs,
                                       //   corpora — any data the paper uses. May be empty.
    { "name": string, "task": string, "role": string }   // role: evaluation, training, pretraining,
                                                          //   primary_data, control, validation, etc.
  ],

  "metrics": [
    { "name": string, "value": string, "dataset": string,
      "comparison": string }          // e.g. SOTA, baseline, ablation, control, placebo,
                                       //   theoretical_prediction, prior_work, none
  ],

  "results": {
    "headline": string,
    "caveats": [string, ...]
  },

  "limitations_stated": [string, ...],
  "open_threads": [string, ...],

  "context_summary": string            // 150-250 words, human-readable narrative
}"""


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + " […truncated]"


def _first_paragraph(text: str) -> str:
    for sep in ("\n\n", "\n"):
        idx = text.find(sep)
        if idx > 0:
            return text[:idx]
    return text


def build_user_prompt(title: str, authors: list[str], year: int | None,
                      abstract: str | None, sections: dict[str, str],
                      category: dict | None = None) -> str:
    parts: list[str] = []
    if category:
        cat_name = category.get("name") or category.get("category_slug") or ""
        cat_desc = category.get("description") or ""
        block = f"# Workspace category\nName: {cat_name}"
        if cat_desc:
            block += f"\nScope: {cat_desc}"
        block += ("\n\nThis paper is being added to the above category. Frame the "
                  "summary so it is maximally useful for downstream gap analysis and "
                  "idea generation *within this category's scope*. Lean toward "
                  "keywords/topics/contributions that are relevant to this scope, "
                  "without inventing claims the paper does not make.")
        parts.append(block + "\n")
    parts.append(f"# Paper\nTitle: {title}")
    if authors:
        parts.append("Authors: " + ", ".join(authors[:8]) + ("…" if len(authors) > 8 else ""))
    if year:
        parts.append(f"Year: {year}")
    if abstract and "abstract" not in sections:
        parts.append("\n## abstract\n" + _truncate(abstract, PER_SECTION_CHARS["abstract"]))

    for name in PRIORITY_SECTIONS:
        text = sections.get(name)
        if not text:
            continue
        if name == "results":
            text = _first_paragraph(text)
        text = _truncate(text, PER_SECTION_CHARS[name])
        parts.append(f"\n## {name}\n{text}")

    parts.append("\n---\n" + SCHEMA_INSTRUCTIONS)
    return "\n".join(parts)
