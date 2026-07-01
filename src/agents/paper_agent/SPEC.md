# Paper Agent — SPEC (living doc)

The paper agent takes a single query (arXiv id, URL, DOI, or title), fetches and
parses the paper into a workspace, then produces a verified structured **summary**
and a verified set of stated research **gaps**. It is a LangGraph pipeline the
main project embeds; it can also be run standalone
(`python src/agents/paper_agent/agent.py <category_slug> <query>`).

## Pipeline

```
query → fetch → extract → summarize → gap → assemble → END
             └─ (fetch ambiguous) → END
```

| Node | Kind | Role |
|---|---|---|
| `fetch` | deterministic (common tool) | Resolve the query to a `paper_id` and download the source. `duplicate` → continue on the existing paper; `ambiguous` → nothing stored, short-circuit to END with the fetch result. |
| `extract` | deterministic (common tool) | Parse the downloaded source (tex tree or PDF) into a flat section map + bibliography + tables, written back into `paper.json`. Idempotent. |
| `summarize` | subgraph | Bounded extractor↔critic cycle → a verified `PaperSummary` + `summary_verification`. See `subagents/summarize/`. |
| `gap` | subgraph | Bounded extractor↔critic cycle → anchor-grounded `GapRecord`s in `gaps/pending/` + `gap_verification`. See `subagents/gap/`. |
| `assemble` | deterministic | Collect the per-stage results into the final `run()` output. |

`fetch` and `extract` are **shared common tools** (`src/common/tools/paper_fetch`,
`paper_extract`), called directly here and by the eval harness. `summarize` and
`gap` are **subgraphs**: each is invoked from its node, so with
`stream(stream_mode=["updates","custom"], subgraphs=True)` every internal
extractor/critic step and LLM call streams through nested under this graph (this
is what the dev UI assembles into its run tree — see
`planning/technical-discussions/LOGGING_SYSTEM.md`).

## Subagents

### `summarize` (Stage 3 + 3b)

`enter → extractor → critic → (regenerate? → extractor : → finalize) → END`

| Node | Kind | Role |
|---|---|---|
| `enter` | deterministic | Read the paper; short-circuit to END if it's already verified (and not `force`); else set up the loop from any prior verification. |
| `extractor` | LLM (reasoner) | One summarization pass → `PaperSummary` (or a field-scoped `PaperSummaryPatch` on a feedback round). |
| `critic` | deterministic grounding + LLM | Deterministic value-in-text grounding check, then one LLM faithfulness/completeness critique. |
| `finalize` | deterministic | Write `summary_verification` and assemble the result. |

The router regenerates while a critique flags a major issue / unmatched grounding
value / missing-coverage hint, bounded by `MAX_SUMMARY_LOOPS` (10) and an early
plateau stop (a round whose still-open set is identical to the previous round's).

### `gap` (Stage 4 + 4b)

`enter → extractor → critic → (converged? → finalize : → extractor) → END`

| Node | Kind | Role |
|---|---|---|
| `enter` | deterministic | Read the paper; short-circuit if gaps already extracted (and not `force`); else set up the static context + loop bookkeeping. |
| `extractor` | LLM (reasoner) + deterministic anchor-grounding | One grounded gap-extraction pass; each candidate's `anchor` is located verbatim in its section to produce the stored quote. |
| `critic` | LLM (reasoner) | One faithfulness critique over the current accepted set; major-issue gaps are dropped, missing-coverage hints fed back. |
| `finalize` | deterministic | Write the accepted `GapRecord`s + `gap_verification`. |

Converges once there's no outstanding cleanup work (nothing rejected this round,
no anchor still needs fixing, no open minor issue) **and** either coverage is
complete or the extractor plateaued — bounded by `MAX_GAP_LOOPS` (10). A minor
issue re-flagged `MAX_MINOR_RECONFIRMS` (2) times is auto-resolved so it can't
exhaust the budget alone.

## Key decisions

- **Subagents stay standalone-callable.** `summarize(category_slug, paper_id, force)`
  and `extract_gaps(...)` compile + invoke their subgraph and return the same
  result dict, so the dev harness and the eval pipeline can exercise a single
  stage in isolation.
- **State isolation across subgraphs.** `summarize` and `gap` are invoked from
  their nodes (not added as shared-state subgraph nodes) so their generic loop
  keys (`result`, `attempt`, `done`, `loop`, …) don't collide with each other or
  the parent — while nested streaming is preserved (confirmed: a subgraph
  invoked inside a node still streams under a `summarize:`/`gap:` namespace).
- **No bespoke logging.** Progress and per-call detail come from the LangGraph
  stream (`updates` for steps, `custom` via `src/common/utils/trace.py` for
  LLM/tool/event leaves); the dev UI reassembles the nested run tree from it.
