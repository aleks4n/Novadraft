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
| `finalize` | deterministic | Attach the `Verdict` to the gap and route it out of `to_validate/`: solved gaps → `gaps/master/` (terminal, kept for dedup); open / partially-solved → `gaps/to_rank/` (the ranking agent's inbox). |

## Gap lifecycle & outputs

Gaps move through per-stage folders under `workspace/<cat>/gaps/` — the folder *is* the
pipeline stage:

```
pending/  →  to_validate/  →  to_rank/   →  master/
(gap agent)   (validator       (ranking      (validated; ranked
               inbox, filled     inbox)        opportunities + solved
               by the director)                gaps land here)
```

- The **director** dedups freshly-extracted gaps in `pending/` against `master/` and drops
  the novel ones into `to_validate/`. The validator drains `to_validate/` and, on finish,
  routes each gap by its verdict: **solved** gaps are terminal and go straight to `master/`
  (kept for the director's dedup, not an opportunity); **open / partially-solved** gaps go to
  `to_rank/`, where the ranking agent scores them and moves them into `master/`. The validator
  never touches `pending/`.
- **`run(category_slug, gap_id)`** is the standard entrypoint: loads the gap from
  `gaps/to_validate/<gap_id>.json`, streams the graph through a `GraphRunLog` (writing the
  per-run `.jsonl` to `workspace/<cat>/logs/validator_agent/`, echoing to a TTY), and returns
  the updated gap record (with `.verdict`). `stream=False` gives a quiet, log-free `.invoke()`.
- **The verdict is appended to the gap**, not written as a separate object: `GapRecord.verdict`
  (`Optional[Verdict]`, `None` until validated). This mirrors the paper.json pattern (one
  record, many agents append their section) and lets the folder lifecycle move a single
  self-contained file. Persistence lives in the `finalize` node (not the `run()` boundary) so
  it also fires when the validator is mounted as a subgraph in the main pipeline.
- **`gap.status` is left untouched (Path A).** It's a gap-agent-authored field (`unchecked` /
  `ambiguous_anchor`, written only by the gap extractor, read by nobody today); the validation
  outcome lives in `gap.verdict.status` and "validated" is signalled by the `master/` folder.
  Repurposing `status` as the single validation-lifecycle field is a possible future cleanup,
  but needs the gap-agent owner's sign-off.

## Key decisions

- **"Model doesn't echo ids":** LLM nodes return judgments only; the code attaches
  `CandidatePaper`/ids. (screen → positions; deep_read → `Assessment`; verdict → `VerdictJudgment`.)
- **The `Verdict` is appended to the gap with assessments kept whole** — `PaperAssessment.paper`
  stays a full `CandidatePaper`, not a slimmed id-reference. The persisted verdict is then
  self-contained (survives `_validator_cache` eviction), and since nothing consumes assessments
  yet, their shape isn't trimmed prematurely; the size cost (a few KB/gap) is moot.
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

## Testing

`workspace/test/` is a committed fixture category (real papers + extracted gaps in
`gaps/pending/`). To exercise the validator end-to-end, seed the inbox from a pending gap
and run:

```
cp workspace/test/gaps/pending/gap_9f0f24c1.json workspace/test/gaps/to_validate/
python src/agents/validator_agent/agent.py gap_9f0f24c1 test
```

The gap lands in `workspace/test/gaps/master/<gap_id>.json` with its verdict attached; that
output is regenerable, so it isn't committed (the run's `logs/` and the prior-art
`_validator_cache` are gitignored).

## Deferred (NOT in V1)
- **Dev-UI registration (pending).** `run()` + `GraphRunLog` + verdict persistence are done;
  what remains is registering `validator_agent` in `dev/registry.py`
  (`GRAPH_BUILDERS` + `LOGGED_AGENTS`) so the dev harness/UI list and render its runs. 
- **Precise prior-art cutoff.** `_cutoff_date` (`tools/search.py`) uses the earliest evidence
  *year* → `YYYY-01-01`. paper.json carries a finer `metadata.publication_date`; switch to it
  (via a `paper_access.get_publication_date`) for a precise cutoff when needed.
- **Gap refinement.** Dropped `Verdict.refined_gap` entirely (was always `None`). Preferred
  future design: an **additive "coverage" array parallel to `evidence`** — each entry names a
  sub-part of the gap and the paper that addresses it — leaving the original gap text stable.
- HyDE, rerankers, ChromaDB — see project memory.
