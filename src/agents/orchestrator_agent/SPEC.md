# Orchestrator Agent — SPEC (living doc)

The orchestrator turns one user turn in the ideation UI into subagent calls, and the
user's subsequent choice into idea nodes. It coordinates the **creative thinker**
(ideation) and the **investigator** (research against real sources), and it is the only
component that writes the idea graph.

Runs after gaps and surveys exist: ideation operates on the gaps the earlier pipeline
produced.

## Graph

```
assemble_context -> plan -> execute -> reflect -> finish
                      ^        |
                      +--------+   (replan / continue)
```

| Node | Kind | Role |
|---|---|---|
| `assemble_context` | deterministic | Focus ids (what the user clicked) → gaps, ideas, and the session's fetched papers. Unresolvable ids go to `missing_ids`, never raise. |
| `plan` | LLM (cheap) | Message + context summary + tool catalogue → `Plan` (goal, tool steps, `done_criteria`). Also clears `results`. |
| `execute` | deterministic | Dispatches each step through `tools/registry.py`. Decides nothing. A failing step is recorded, the rest continue. |
| `reflect` | LLM (cheap) | Results vs `plan.done_criteria` → route and the user-facing answer. |
| `finish` | deterministic | Stamps provenance, writes the run result (answer + drafts) to the session file. |

Accepting a draft is **not in the graph** — `commit_selection()` is a plain function
(no LLM, no plan, no reflection) that the UI calls when the user clicks.

## Key decisions

- **A run always completes; it never suspends mid-graph.** A run ends by *offering*
  drafts; accepting them is a separate call. Pending state lives in a session file rather
  than a LangGraph checkpointer, so this keeps the same one-run-per-call / `GraphRunLog`
  shape as the validator and ranking agents. Revisit only if the thinker ever needs to
  pause *mid-tree* and resume the same tree — end-of-run escalation does not need it.
- **An idea node is created only by an explicit user action.** Agents produce
  `status="draft"` ideas that live in the session; `commit_selection` is the sole path to
  `ideas/`, and it only writes ids that the named run actually offered.
- **The orchestrator is free, not a fixed set of operations.** It gets a goal and a tool
  catalogue and composes them itself. Safe because the two boundaries that matter are
  typed: the tool call schemas, and the commit gate. It stops being safe if the
  orchestrator gains a direct write path, or a tool takes free text where it took an enum.
- **The tool descriptions in `tools/registry.py` are the specification.** `catalog()`
  renders them into the planner's prompt — changing a description changes what the
  orchestrator knows how to do. That file is the design surface, not a list of operations.
- **`done_criteria` must be structural, not qualitative.** Counts, presence, distinctness —
  things checkable by looking at what came back. Quality criteria ("names a concrete
  mechanism", "is novel") have no ground truth at this step, and reflect loops to the
  iteration cap trying to satisfy them: measured at 6 LLM calls per turn before the
  prompts were tightened, 2 after. Whether an idea is *good* is decided by evaluation and
  by the user, later.
- **Context is assembled by code, never fetched by an LLM.** The planner decides *what to
  do*, not *what to read*.
- **Reflect sees the same context summary as the planner.** Without it, "nothing was
  selected" and "the tool returned nothing" are indistinguishable, and it reports the
  wrong one.
- **`plan` clears `results`.** A new plan is a fresh attempt, so the previous attempt's
  drafts must not be offered alongside it. On a `continue` route the graph goes straight
  back to `execute`, where results accumulate — which is what that route means.
- **`plan` runs on `cheap` (deepseek-chat), not the reasoner.** Measured: the reasoner
  answers in prose instead of emitting the tool call often enough to fail outright ~1 in 3
  times *even with* the registry's re-ask, and is slower (9–11s vs 5–9s), while producing
  the same plan shape.
- **`run()` reads its result back from the session file.** `GraphRunLog.run()` returns only
  the output it was handed, and the streaming path hands it none — the same reason the
  validator re-reads its finalized gap.
- **A dangling parent edge is dropped, not written.**

## Idea ontology (V1 — deliberately minimal)

```
gap.json  --addresses-->  idea.json  --child-->  idea.json
```

One `Idea` type for drafts and committed nodes, distinguished by `status`. Ideas get their
id from the agent that creates them (not from the LLM inside it), so the UI can name
exactly what the user ticked and rejected drafts stay referenceable.

Edges live on the *child* (`parents`), so a write is one file and concurrent sessions never
contend. There is no central graph file — `common/tools/idea_graph.py` assembles the graph
on read (`for_gap`, `children_of`, `ancestors`, `roots`).

`thought_graph` is `list[dict]`, **opaque to the orchestrator** — the creative thinker owns
that shape.

No `variant`, no `merge`, no version chain yet. Add them when real sessions show the need.

## Contract with the creative thinker

`common/schemas/thinker.py` — shared, cross-team; changing it is a cross-team change.

```
thinking_mode   generation (fan-out) | refinement (tree / chain of thoughts)
context         Context: gaps + ideas + session papers
instruction     the orchestrator's own words for this call
lenses          personality presets, each prepended to the thinker's prompt
guard           width/depth — FIXED for now, not exposed to the planner
tool_use        may the thinker call the investigator itself?
```

Returns `CreativeThinkerResult`: `ideas` (drafts), `text`, optional `escalation`. All of it
is an offer — the thinker never writes.

## Storage

```
workspace/<category>/
  ideas/<idea_id>.json                    one file per node, additive
  sessions/<session_id>/<run_id>.json     run result: answer + pending drafts (gitignored)
  sessions/<session_id>/papers/           investigator fetches for this session
  logs/orchestrator_agent/<run>.jsonl     GraphRunLog (gitignored)
```

## Status

Registered via `MANIFEST` in `__init__.py` (auto-discovery — no edit to `dev/registry.py`),
so it appears in the dev harness and the app backend. Everything is real except the
creative thinker.

- **`tools/dummy_thinker.py`** — deterministic, LLM-free stand-in. One draft per lens from
  the gap's own text, plus a small thought graph; refinement round-trips the same idea.
  Swap point: the `think` import in `tools/registry.py`.
- **investigator** — the real `src/agents/investigator_agent` is wired, called lazily.

```
python src/agents/orchestrator_agent/agent.py "<message>" <category> [gap_id ...]
```

Demo data: use a `gaps/master/` folder (`llm_reasoning`, `computer_vision`,
`generative_adversarial_network`). **97 of 106 gaps in `transformers/pending/` predate the
current `GapRecord`** (no `evidence[].anchor`) and land in `missing_ids`.

## Open / deferred

- **The investigator writes to the wrong place.** It treats `session_id` as a category —
  `workspace/<session_id>/`, with a `meta.json`, so every session appears in `/api/topics`
  as a research topic. Until reconciled, only its returned text is used and fetched papers
  don't reach the next turn's context. Cross-team fix.
- **The frontend expects an agent named `idea_agent`.** `app/backend/main.py` posts to
  `MANAGER.start(slug, "idea_agent", ...)` with `{mode, sketch, count}` — a button, not a
  chat, and no draft-acceptance step. Decide whether to rename the endpoint or adapt.
  `mappers.map_idea` also expects the old flat schema; it now derives `relatedGaps` from
  `parents` so both render, but the evaluation scores it wants don't exist on `Idea`.
- **Investigator findings have no destination.** A direct research call returns prose that
  is not an idea and not a gap; today it evaporates with the chat message.
- **Multi-parent ideas.** Combining two ideas needs two parents; the schema allows a list
  but nothing produces one.
- **Refinement semantics.** The thinker updates an idea in place, so anything attached to
  the old text is silently invalidated. In-place plus thought-graph history, or a version
  chain — not yet decided.
- **Per-session budget.** `_MAX_ITERATIONS` caps the reflect loop; nothing caps total LLM
  calls or fetches across a session.
