# Ranking Agent — SPEC (living doc)

The ranking agent scores one **validated opportunity** gap on three independent
dimensions — **openness**, **impact**, **feasibility** — and moves it into the master
gap list with those scores attached. It is a linear, single-gap LangGraph subgraph the main
project embeds as a node; it can also be run standalone
(`python src/agents/ranking_agent/agent.py [category_slug]` ranks every gap in `to_rank/`).

## Pipeline

```
gap → score_openness → score_impact → score_feasibility → finalize → END
```

| Node | Kind | Role |
|---|---|---|
| `score_openness` | deterministic | `gap.verdict` (status + confidence) → openness in [0, 1]. `open` scores high, `partially_solved` low. |
| `score_impact` | deterministic (+ cached LLM) | Per-evidence-paper venue + year (from `paper.json` via `paper_access`) → `impact = quality(venue, citation) · recency · evidence_bonus`. Venue names are resolved to CORE acronyms in one batched, cached LLM call. |
| `score_feasibility` | LLM (reasoner) | Judge how tractable making progress on the gap is → `FeasibilityAssessment` (score + reasoning). |
| `finalize` | deterministic | Attach a `RankingScores` (the three raw scores) to the gap; move it `gaps/to_rank/` → `gaps/master/`. |

## Gap lifecycle & outputs

Ranking sits after the validator in the shared gap folder lifecycle (see the validator SPEC):

```
… → to_rank/ → master/
     (ranking    (ranked opportunities,
      inbox)      each with a RankingScores)
```

- **`run(category_slug, gap_id)`** is the standard entrypoint — one gap, mirroring the
  validator: loads it from `gaps/to_rank/` (open / partially-solved, carrying its `verdict`),
  streams the graph through a `GraphRunLog` (`workspace/<cat>/logs/ranking_agent/`), and returns
  the updated gap record (with `.ranking`). `stream=False` gives a quiet `.invoke()`. The
  orchestrator (and the standalone `__main__`) loop it over `to_rank/`.
- **`rescore_impact(category_slug, gap_id)`** recomputes **only** the impact dimension of an
  already-ranked gap in `master/` and updates its `RankingScores` in place — for the director to
  call after merging a corroborating paper. Adding evidence shifts impact (evidence bonus + the
  new paper's venue/recency) but not openness (the verdict is unchanged) or feasibility, so those
  are kept and **no reasoner call fires**.
- **Scores are appended to the gap**: `GapRecord.ranking` (`Optional[RankingScores]`, `None`
  until ranked) — mirrors `verdict`. Solved gaps never reach ranking (the validator parks
  them in `master/`), so they carry no `RankingScores`.

## Key decisions

- **Three raw scores persisted, no `final_score`.** `RankingScores` holds `openness`,
  `impact`, `feasibility` (+ `feasibility_reasoning`) — deliberately **not** a combined final
  score. The final ordering is a weighted geometric mean of the three, and the weights are
  **user-tunable at view time**, so re-weighting must never require re-scoring. The agent
  therefore does no weighting, no ordering, and no top-X truncation — those are view-time
  (UI) concerns.
- **No filtering at compute time.** Every gap in `to_rank/` is scored and moved to `master/`;
  the `min_*` floors in `RankingConfig` are view-time filters, not pipeline gates. So the
  scoring nodes don't drop gaps.
- **Openness is derived from the validator verdict**, read straight off `gap.verdict`
  (status + confidence) — no separate verdicts input.
- **Impact reads paper facts straight from `paper_access`** — `get_venue` + `get_year`, keyed
  by `(category_slug, paper_id)`. No separate metrics module: a gap has few evidence papers and
  paper.json reads are cheap. Citation has no source yet (no OpenAlex enrichment) → `None`, so
  impact runs on the venue / recency / evidence signals it can derive.
- **Venue → CORE acronym via a batched, cached, grounded LLM call** (`venue/resolver.py`):
  the model normalizes messy names to acronyms, answers are grounded against the CORE list
  (a hallucinated acronym can't produce a rank), and name→acronym is cached to disk. Journals
  / unknowns resolve to no venue score. Uses the registry's `chat_with_structured_output`.
- **LLM calls go through the shared registry helper** `chat_with_structured_output` (not
  `llm.with_structured_output(...).invoke`) — same reason as the validator (deepseek-reasoner
  400s on a forced tool_choice). Structured-output schemas default their fields so a dropped
  field (e.g. the model omitting a journal's acronym) degrades gracefully instead of raising.

## Testing

`workspace/test/` is a committed fixture category with real papers. To exercise the agent,
put open / partially-solved gaps (with verdicts) in `gaps/to_rank/` and run — the realistic
path is to run the validator first so it populates `to_rank/`; a couple of hand-built
`to_rank/` gaps referencing real test papers work too. Then:

```
python src/agents/ranking_agent/agent.py test
```

Ranked gaps land in `workspace/test/gaps/master/<gap_id>.json` with a `ranking` block; these
run outputs are regenerable and not committed (`logs/` and the venue `.cache/` are gitignored).

## Deferred (NOT in V1)

- **Citation signal.** Needs upstream OpenAlex enrichment writing citation percentiles into
  `paper.json`; until then `impact` runs without it. When it lands, add a `paper_access.get_citation`
  getter and read it in `score_impact` (pass it into `_quality`).
- **Tuning the impact-internal blend.** Only the three *dimension* weights are user-tunable
  today. To let the user also tune impact's internal knobs (venue/citation blend, recency,
  evidence bonus), persist the `ImpactBreakdown` alongside the scores.
- **Parameter tuning** (weights / floors / recency half-life) — defaults ship; refine on main.
- **Dev-UI registration** — register `ranking_agent` in `dev/registry.py`
  (`GRAPH_BUILDERS` + `LOGGED_AGENTS`), same follow-up as the validator.
