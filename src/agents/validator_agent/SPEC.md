# Validator Agent — SPEC (living doc)

The validator takes one research **Gap**, searches the literature published *after*
the gap's prior-art cutoff, and judges whether the gap has since been addressed.
It is a linear LangGraph subgraph the main project embeds as a node; it can also be
run standalone (`python src/agents/validator_agent/agent.py`).

## Pipeline

```
gap → query_gen → search → rrf → screen → deep_read → verdict → END
```

| Node | Kind | Role |
|---|---|---|
| `query_gen` | LLM (cheap) | Gap → diverse arXiv queries (literal / reframed / broad), each anchored to a domain term from `gap.source_keywords`. |
| `search` | deterministic | Run each query against arXiv (date-filtered, retry/backoff); one ranked list per query. |
| `rrf` | deterministic | Reciprocal-rank-fusion the lists → 15 deduped candidates. |
| `screen` | LLM (cheap) | Abstract-level triage → ranked shortlist (≤ 8). |
| `deep_read` | LLM (reasoner) | Per-paper full-text read → `PaperAssessment` (≤ 5; download misses backfilled). |
| `verdict` | LLM (reasoner) | Synthesize the assessments → one `Verdict` for the gap. |

## Key decisions

- **"Model doesn't echo ids":** LLM nodes return judgments only; the code attaches
  `Paper`/ids. (screen → positions; deep_read → `Assessment`; verdict → `VerdictJudgment`.)
- **Structured output via `method="function_calling"`** everywhere — DeepSeek doesn't
  support OpenAI's `json_schema` `response_format`.
- **Prior-art cutoff** = earliest `Evidence.published_date`; search looks strictly after it.
- **Verdict status is LLM-judged**, not mechanically derived from relevance labels —
  multiple `partial_solution`s may or may not add up to `solved`, which a label map can't capture.
- **Empty short-circuit:** if `deep_read` produced no assessments, `verdict` returns a
  deterministic `open` verdict and skips the LLM call.
- **Search/fusion favour recall, not precision.** Downstream steps (screen, deep_read)
  re-read and filter, so a missed paper is lost for good while a loosely-relevant one is
  harmless. Two consequences:
  - query_gen `broad` is a genuine high-recall query (1–2 short, high-level terms, *not*
    `literal` with synonyms stripped); group count may vary per query (more AND-groups =
    narrower). Tuned over repeated runs where `broad` kept collapsing into `literal`.
  - RRF uses `k=10` (not the standard 60). High k flattens the rank curve so cross-query
    *consensus* wins and a unique-angle query gets buried (observed: a query contributing
    0 of 15 candidates). k=10 lets a strong single-list hit survive. Not lower (k≤5):
    a thin query list — e.g. 4 papers — would then flood the candidate pool, since rank
    within a short list is a weak signal. k=10 fixes the shut-out without the flooding.
    Tuned via an offline k-sweep over saved `step_02_search.json` traces.

## Deferred (NOT in V1)

- **Gap refinement.** `Verdict.refined_gap` exists in the contract but the verdict node
  always sets it to `None` for now. Continuously rewriting `gap.description` makes the
  gap a moving target and loses history. Preferred future design: an **additive
  "coverage" array parallel to `evidence`** — each entry names a sub-part of the gap and
  the paper that addresses it — leaving the original gap text stable.
- HyDE, rerankers, ChromaDB, PDF full-text (interim downloader is LaTeX-only) — see
  project memory.
