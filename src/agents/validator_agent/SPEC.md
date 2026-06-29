# Validator Agent — SPEC (living doc)

The validator takes one research **Gap**, searches the literature published *after*
the gap's prior-art cutoff, and judges whether the gap has since been addressed.
It is a linear LangGraph subgraph the main project embeds as a node; it can also be
run standalone (`python src/agents/validator_agent/agent.py`).

## Pipeline

```
gap → query_gen → search → rrf → screen → deep_read → verdict → finalize → END
```

| Node | Kind | Role |
|---|---|---|
| `query_gen` | LLM (cheap) | Gap → diverse arXiv queries (literal / reframed / broad), each anchored to a domain term from the source papers' `summary.keywords` (via `paper_access.get_keywords`). |
| `search` | deterministic | Run each query against arXiv (date-filtered, retry/backoff); one ranked list per query. |
| `rrf` | deterministic | Reciprocal-rank-fusion the lists → 15 deduped candidates. |
| `screen` | LLM (cheap) | Abstract-level triage → ranked shortlist (≤ 8). |
| `deep_read` | LLM (reasoner) | Fetch each shortlisted candidate into `_validator_cache` (paper_fetch + paper_extract, cache-reused), read its full text → `PaperAssessment` (≤ 5; fetch misses backfilled). |
| `verdict` | LLM (reasoner) | Synthesize the assessments → one `Verdict` for the gap. |
| `finalize` | deterministic | Persist the `Verdict` to `verdicts/<gap_id>.json` — the handoff the ranking agent reads. |

## Entrypoint & outputs

- **`run(category_slug, gap_id)`** is the standard entrypoint: loads the gap from
  `gaps/pending/<gap_id>.json`, streams the graph through a `GraphRunLog` (so it writes the
  standard per-run `.jsonl` to `workspace/<cat>/logs/validator_agent/` and echoes to a TTY),
  and returns the `Verdict`. `stream=False` gives a quiet, log-free `.invoke()`.
- **Verdict persistence** is a `finalize` node, not the `run()` boundary, so it also fires
  when the validator is mounted as a subgraph node in the main pipeline. The scoring nodes
  stay pure; `run()` reads the persisted verdict back to return it.

## Key decisions

- **"Model doesn't echo ids":** LLM nodes return judgments only; the code attaches
  `CandidatePaper`/ids. (screen → positions; deep_read → `Assessment`; verdict → `VerdictJudgment`.)
- **LLM calls go through the shared registry helper** `chat_with_structured_output(llm,
  system, user, schema)` (not `llm.with_structured_output(...).invoke`). It steers DeepSeek
  via function calling and leaves `tool_choice="auto"` — the `reasoner` model 400s on a
  forced tool_choice ("thinking mode does not support this").
- **Prior-art cutoff** = earliest source-paper **year** (`paper_access.get_year` over the
  gap's evidence papers) → `YYYY-01-01`; search looks after it. paper.json carries only the
  year, which suffices here.
- **Prior-art candidates are cached** in a shared hidden workspace category `_validator_cache`:
  deep_read fetches each via `paper_fetch` + `paper_extract` once and reuses it across gaps
  (`PaperAlreadyExistsError` = cache hit). The leading `_` hides it from `list_workspaces`
  (one-line skip added there) so the UI doesn't list it as a research topic.
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

## Integration with `main` (schema migration)

Built first against interim stub schemas; migrated to the real shared contracts on merging `main`:

- **`Gap`/`Evidence` → `GapRecord`/`GapEvidence`** (main's schemas). The two fields the
  validator needs but the gap doesn't carry are read from the **source paper's `paper.json`**
  via getters — not added to the gap:
  - `published_date` → `paper_access.get_year(category_slug, paper_id)`
  - `source_keywords` → `paper_access.get_keywords(...)` (`summary.keywords` + `topics`)
- **`Paper` (interim) → `CandidatePaper`** (new, in `common/schemas/paper.py`): a lightweight
  external-search-hit view, distinct from the heavy workspace `PaperRecord`. Lives in common
  because the cross-agent `Verdict` carries it.
- **`category_slug` threaded through `ValidatorState`** — getters are keyed by
  `(category_slug, paper_id)` since the workspace partitions papers by category.
- **`paper_access.py`** (new, `common/tools/`): typed read accessors over `paper.json`
  (year / venue / keywords / sections / full_text), validating `PaperRecord` on read.

## Deferred (NOT in V1)

- **Test fixtures (pending).** Getters read `workspace/<cat>/papers/<id>/paper.json`, so the
  old self-contained `test_gaps` no longer suffice — need either a small fixture workspace
  (source-paper stubs with `metadata.year` + `summary.keywords`; the validator never reads
  source content) or mocked `paper_access`, plus the gaps re-expressed as `GapRecord`.
- **Dev-UI registration (pending).** `run()` + `GraphRunLog` + `verdicts/`
  persistence are done; what remains is registering `validator_agent` in `dev/registry.py`
  (`GRAPH_BUILDERS` + `LOGGED_AGENTS`) so the dev harness/UI list and render its runs. 
- **Gap refinement.** Dropped `Verdict.refined_gap` entirely (was always `None`). Preferred
  future design: an **additive "coverage" array parallel to `evidence`** — each entry names a
  sub-part of the gap and the paper that addresses it — leaving the original gap text stable.
- HyDE, rerankers, ChromaDB — see project memory.
