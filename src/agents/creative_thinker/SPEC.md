# Creative Thinker — SPEC (living doc)

The **divergent** worker of the idea agent. It produces and develops candidate ideas
rather than answering questions (that is the Investigator, the convergent worker). It is
a **pure, callable worker with no side effects**: the orchestrator calls it, it thinks,
it returns one envelope.

- No workspace access, no session state, no chat channel. It cannot create an idea,
  write a file, or speak to the user.
- **Stateless across calls** — everything it knows about the idea's history comes from
  the copy it was handed, specifically that copy's `runs[]`.
- **Anytime** — stopped at any round it still returns a usable result, labelled partial.

```
prepare ─┬─ generate                    fan-out: `width` styles in parallel, depth 1
         └─ refine [subgraph]           tree of thoughts: expand → evaluate ⟲, ≤ `depth`
                     └→ writeback
         └→ assemble → END
```

There is no third mode: a chain of thought is `refinement` with `depth = 1` through this
same code path, and a graph of thoughts is refinement plus the aggregate operator.
Merging is refinement seeded from two source ideas.

## Refinement is driven by a task

`spec.task` is the free instruction the orchestrator passes down, and it is what makes
one mode serve everything a session asks: **a question · a change · an addition · a
critique · an open develop**. Every branch is a way of *doing the task*; the idea's own
recorded `open_questions` are supplied as material, not as the agenda.

Before this existed, `expand` branched on `sections["open_questions"]` and nothing else.
That made refinement a single plan schema — "deep dive" — wearing the costume of a mode:
you cannot answer *would this work for video?* by expanding the idea's own navel. The
task is threaded into `expand`, `select_branch` and `apply_edits` alike, and a test
asserts it reaches all three.

Because a run is now *asked* something, it **owes an answer**: `envelope.answer`.

## Nodes

| Node | Kind | Role |
|---|---|---|
| `prepare` | deterministic | Deep-copy the idea snapshot; open the trace and the budget ledger; render context, idea and prior ground; mint the root thought node. |
| `generate` | LLM ×width + deterministic | `width` differently-styled thinkers in parallel, one rough candidate each; dedup (batch + context), optional grounding pass for sources, carve drafts. **No ranking** — the batch comes back in the order it was thought of. |
| `refine/expand` | LLM | One level of the tree: up to `width` ways of doing the task from here. A branch needing a fact asks the Investigator or leaves an `investigate` proposal. |
| `refine/evaluate` | LLM ×1 + deterministic | **One call over the whole frontier** → the branch to deepen, a verdict line per branch, and a child/variant label per branch. `undecided` is a first-class answer. |
| `refine/writeback` | LLM | Answer the task; record the result in the **one** idea through section ops; rewrite the description/problem **only when the task asked for it or the run made what is written there wrong**; recommend anything further as a `proposed_change`. |
| `assemble` | deterministic | Append the run to `runs[]`, build the `RunSummary`, compute metrics, build the envelope. The only exit. |

## Inputs

The slots the orchestrator fills (`ThinkerCall`), plus a `Budget`. The plan schema — not
the thinker — decides them, and the thinker never changes them mid-run.

| Slot | Meaning |
|---|---|
| `mode` | `generation` or `refinement` |
| `idea` | a full copy of one `idea.json`; the parent in generation, the idea under work in refinement |
| `context` | whatever the plan loaded: gaps, papers, ideas, user input, extracted text |
| `task` | **what this run should do**, in the orchestrator's words |
| `styles` | the agent style presets to think with, each with its own model/decoding |
| `width` | branches per expansion. Generation: the size of the fan-out (and so the number of distinct styles). Refinement: the branching factor per level, capped at 6 |
| `depth` | refinement only: levels of the tree. `1` degenerates to a chain of thought. Generation is `1` by definition |
| `tool_use` | when true the thinker may call the Investigator mid-branch |

**Why two numbers.** One `n` meaning "breadth here, depth there" could not express a
refinement that branches *and* deepens, which is what a tree of thoughts is; the
branching factor was whatever the model happened to return. A legacy `n` is still
accepted at both harness entry points and on stored `RunHeader`s, read onto whichever
axis its mode used it for, so old fixtures and old traces still load.

## Copy semantics

The `idea` argument is a **snapshot**. `prepare` deep-copies it and every edit lands on
the copy, which is returned; the caller's object is byte-identical afterwards
(`test_input_object_is_not_mutated`). Two things may change on the copy:

- **content** — `description`, `problem` and the open `sections`, in refinement only;
- **the thinking graph** — `runs[]`, in both modes.

`runs[]` is **append-only and mandatory**. Exactly one entry per invocation, appended
even when nothing was accepted: a run that produced nothing usable is still the record
of what was tried. `tools/trace.append_run` is the only writer, and it only appends —
existing entries are never edited or reordered.

A **new** idea is never created here. It comes back as a draft `IdeaRecord` with
`idea_id = None`, no relations, and an `origin` block pointing at the node and run that
produced it. Minting ids and writing edges is the orchestrator's job.

### `problem` is always written

Every record this agent hands back states its `problem` — generation drafts included.
The generation thinker is asked for it as a first-class output (`Candidate.problem`) and
the writeback is asked to restate it on every refinement run, changed or not; both paths
then fall back to the one-liner rather than let a spine field land empty. `problem` is
what the orchestrator ranks and groups on, so an empty one is a hole nothing downstream
can fill.

### Children and variants inherit the whole record

A draft produced from a **parent** — a generation fan-out seeded with one, or a
refinement split — starts as a full copy of that parent: `problem`, `addresses`,
`sections`, `evaluation`, `provenance` **and `runs[]`** (`agent._inherit`). The
candidate's own contribution is written over the parts it actually changes.

The alternative — a pointer back to the parent — produced a child that read as a stub
the moment you opened it: a title, a one-liner, and nothing else. A child *is* an idea
and has to stand on its own, and it is a variant *of that thinking*, so the thinking
comes with it. `origin.inherited_from` / `inherited_upto_run` mark where the copy stops,
so the shared prefix stays identifiable once the two files diverge.

On acceptance the orchestrator appends the carved branch of the current run on top of
that inherited history (`idea_agent.apply._carve_subgraph`), so the child's graph reads
continuously from the parent's first run to the branch that split it off.

**Generation from a parent produces variants**, structurally: each styled thinker
attacks the parent's gap with its own mechanism, which is what a variant is. A *child*
— the same mechanism made more specific — only ever comes out of refinement, where the
selector can see the mechanism being narrowed.

## The result envelope

| Field | Contents |
|---|---|
| `idea` | the modified copy — content edits plus this run appended to `runs[]` |
| `answer` | **the run's reply to `task`** — substance, not phrasing |
| `proposed_changes` | changes to *this* idea the run recommends but did not make, because it was not asked to: `target` (`description` / `problem` / a section slug), `change`, `reason` |
| `drafts` | generation candidates, or the sub-graph carved out by a suggested split |
| `suggested_operations` | `update-idea` · `create-child` · `create-variant` · `merge` · `investigate`, each with the branch it came from, a one-line diagnosis, the rationale that would become the edge's justification, and an optional `group` |
| `questions` | open questions for a human, pinned to the node that raised them, with the options weighed |
| `status` | `completed` · `stopped-uncertain` · `truncated`, with the reason and the limit that tripped |
| `metrics` | calls/investigations spent, and diversity (mean pairwise distance, cluster count) |

The envelope is always complete: a run that accepted nothing still returns a valid `idea`
with its trace appended and an empty `drafts`.

**The orchestrator still owns the voice.** `answer` is what the run found, not how the
user should hear it; the orchestrator is the only component that speaks. The write-up
call runs even when no branch survived, because *"nothing could be settled, and here is
why"* is a real answer — it is the record of what happened, not a reward for succeeding.

## The run summary — what the *next* run reads

Every run entry carries a `RunSummary { objective, accepted, rejected, open }`, built by
`assemble` from the trace so it can never disagree with it.

`runs[]` grows forever, so it cannot be what the next prompt is built from: pasting every
pruned branch into the context means run twenty is mostly a recitation of runs one
through nineteen. `tools/context.prior_ground` therefore renders at **two resolutions** —
every run as its summary, and only the last two runs' raw `pruned`/`rejected` branch text,
where the detail is still worth its tokens. The full graph stays on the record as
evidence for the decision-graph window.

This closes the vault's own open question, *"how `runs[]` is kept from swallowing the
file"*.

## Sections are open, and named by the thinker

`sections` is the flexible half of `idea.json` — the design of record calls it
*"deliberately not validated"*, because what matters about an idea (a technique, the
baselines, a napkin formula, a discussion that settled something) differs per idea and
is not knowable up front.

**Generation names its own.** `Candidate.sections` is a `list[Section]` of `{key, value}`
and the starter vocabulary lives in the prompt, not the schema. It used to be three
literal fields — `technique`, `open_questions`, `rationale` — copied into `sections`
verbatim, which is why every idea in the workspace carried exactly those three and
refinement had nothing but those three slots to overwrite. A `{slug: value}` map is
accepted, and any key a model leaves at the top level outside the closed spine
(`title`, `one_liner`, `description`, `problem`, `sections`) is folded in as a section
rather than dropped: pydantic's default would turn that drift into an idea with no
content at all.

**Refinement changes them through named operations.** `apply_edits` returns
`edits: list[SectionEdit]` — `set` · `append` · `delete` · `rename` · `reorder` —
applied deterministically by `apply_ops`. A `set` on a key that does not exist yet
**creates** it, and that is the normal way a run records something new.

The previous writeback returned a `{slug: markdown}` blob merged additively, which meant
a section could never be **deleted**: the record only grew, and pruning it needed a human
editing the file. An omitted key still means *leave it alone*; removing something has to
be said out loud. No section is protected — `open_questions` is *reserved* only in the
sense that `agent._open_questions` reads it by name, and a run that decided nothing is
open is entitled to empty it. A flat `{slug: value}` map is still accepted and read as
all-`set`, because losing a whole run to formatting is worse than the ambiguity.

## Do what you were asked, and only that

The task's grammar decides how far the write-up may reach, and the write-up is where
that is enforced:

- **A question** — *would this hold for video? · what if we used flow matching?* — is
  answered, and what the run established is recorded in the sections, usually under a
  new key named for its subject. The **description and problem are left alone**.
- **A change** — *make it cheaper · rewrite the description · adopt the v-prediction
  drift* — is made, spine included. That is the job, not an overstep.
- **An addition / a critique** — added, or recorded as the critique it is. A critique
  does not silently become a revision of the claim.

A question phrased as a proposal is still a question: *"what if we learned the velocity
instead of the score?"* asks the run to evaluate that, not to adopt it. The description
and problem are rewritten in exactly two cases — the task asked for it, or the run made
what is written there **wrong**. "It could be sharper now" is a `proposed_change`.

**`title` and `one_liner` are editable, and only on request.** `IdeaEdit` carries both,
empty meaning *leave it alone*, and `merge_edit` writes them when they are not. They were
missing from the schema while the prompt already described them as fields of their own,
so *"rewrite the one-liner"* was a task the write-up could not carry out: the model put
the new sentence in `answer`, nothing applied it, and the run returned a record identical
to the one it started from — a change the user asked for, reported as done, and never
made. A run that was not asked still leaves both alone; a line that no longer fits the
idea is a `proposed_change`, not a retitle on the run's own initiative.

Before the split, writeback rewrote the description unconditionally: across every logged
run it did so 5 times out of 5, including for *"what problem does this idea attack"*,
which needed no edit at all and had its `problem` rewritten too. A question came back as
a fait accompli instead of an answer, and — because the rewrite smoothed drift into
coherent prose — the divergence check downstream had nothing left to notice.

## `proposed_changes` — recommend without acting

A run that answers a question may find something that argues for a change it was not
asked to make. It says so, in `proposed_changes` (`target`, `change`, `reason`), and
leaves the idea alone.

The split follows what each component can see. The thinker can tell a question from a
command — that is grammar, and it is in the `task` string. It **cannot** tell whether a
recommendation should be adopted: that needs the conversation, what the user has already
decided, what else is in flight. So the thinker recommends and the **orchestrator**
decides — resolving it from context it holds, or putting it to the user. A "yes" comes
back as the next run's `task`, and *that* run makes the edit, because that run was asked
to.

It is deliberately **not** a `SuggestedOperation`: those are proposals about the idea
*graph* (a new node, a new edge), and `update-idea` in that vocabulary is already the
orchestrator's. This is content, on one record, and needs no new escalation type.

Proposals are persisted on the run entry (`outcome.proposed_changes`) and rendered into
the next run's prior ground, so a standing recommendation is visible instead of being
re-derived and proposed a second time.

## Stopping

The thinker judges itself, except where a wrong autonomous call would quietly corrupt
the idea graph — there it stops and hands the decision **up to the orchestrator**, never
to the user.

- **Genuine uncertainty** — the selector returns `undecided` → `stopped-uncertain`, with
  **every** branch left `kept`, one question carrying them as options, and a grouped
  `create-variant` proposal per branch. The user then either picks one or takes them all
  as variants. Never an arbitrary pick.
- **Divergence** — a branch the selector labelled `child` (same mechanism, made specific)
  or `variant` (same gap, different mechanism) is escalated even though it lost. `none`
  is the common, correct answer.
- **A knowledge gap it may not close** — a branch that declared `needs_fact` and could
  not have it settled (no Investigator wired, `tool_use` off, budget spent, or the
  Investigator came back empty) → an `investigate` proposal carrying the concrete
  question. It never invents the answer.

  **Generation escalates only a failed look-up.** It used to raise one from the top
  candidate's first `open_questions` entry whenever `tool_use` was off — which is
  every run by default, since every candidate carries open questions and that is what
  the field is for. The same card each time is one the user learns to skip, and it
  costs the escalations that do mean something. Open questions are the normal
  furniture of a rough candidate, already on the record in the idea's
  `open_questions` section; what earns a proposal is the thinker *trying* to settle
  something and failing.
- **A ceiling** — budget or time → `truncated`, returning the last *completed* round's
  survivors and naming the limit.

**Rate limit.** At most one *incidental* split proposal per run (variant outranks child)
and at most one `investigate` — `tools/envelope.rate_limit_escalations`. An agent that
flags every ambiguity is worse than one that never does. **Grouped proposals are exempt**:
the variant-per-branch offer raised by an undecided round is one offer wearing several
operations, and thinning it to its strongest member would answer the question the run
explicitly declined to answer.

### Depth is not a truncation

Reaching the requested `depth` ends the run **`completed`**: the plan asked for a tree of
that depth and got one. `truncated` is reserved for ceilings the plan did *not* ask for —
the budget or the clock — so `truncated` always means "less than what was asked for",
which is what makes it useful to the reflector.

## One evaluator call, not a round-robin

`select_branch` sees the idea, the task and **every sibling at once**, and returns the
index to deepen plus a verdict line and a divergence label per branch. It replaced
`compare_siblings` (pairwise votes) + the round-robin arithmetic that once lived in
`tools/rank.py` (since deleted with the rest of the ranking) +
`diagnose_divergence` (a separate call to label a near-tie). Three reasons:

- **Cost** — `k` siblings cost `k(k-1)/2` comparisons for a judgement one call can make.
- **Honesty** — round-robin arithmetic can crown a branch no single comparison endorsed
  (a → b, b → c, c → a resolves to a "winner" the model never expressed).
- **Shape** — only a call seeing the whole frontier can tell three facets of one idea
  from three genuinely different ideas, which is exactly what decides whether an
  undecided round becomes *pick one* or *make a variant of each*.

The model is still only ever asked a **relative** question and never scores a branch. A
self-contradictory answer — an out-of-range index, no winner, or `undecided` alongside a
winner — resolves to *undecided*, never to branch 0: inventing a decision is the one
failure mode that is invisible afterwards.

## Styles

The diversity mechanism is prompts, not temperature. Each style is a `styles/<name>.md`
giving a stance, its preferred moves, what it considers a good idea, and what it refuses
to do; a style may carry its own model and decoding settings. Starting roster:
`optimizer`, `combiner`, `skeptic`, `transferer`, `minimalist`, `extremist`, plus
`neutral` for refinement. Breadth wants variety (generation fans out over distinct
styles), depth wants consistency (refinement runs one). Every node records the style
that produced it, which is what makes the roster measurable over time.

## What is deliberately not in the model

Dedup, budget accounting, diversity metrics, trace assembly, the
section-edit application and the escalation rate limit are all deterministic (`tools/`).
The model is asked only for judgements it is uniquely able to make, and always *relative*
ones — "which of these", never "score this out of ten".

## Streaming and logging

The graph is streamed, never invoked quietly. `prepare`/`expand`/`evaluate`/`writeback`
emit `branch created` / `branch pruned|kept|accepted` /
`investigator called` / `escalation raised` through `trace.emit`, so a long run shows its
tree growing. LLM calls trace for free via `src/llm/registry`. `assemble` returns the
envelope as a top-level `result` delta, which is what the run log records as the run's
output — so a replayed `.jsonl` reconstructs the same tree it streamed.

`investigator called` is emitted **only for calls that happened** (`Finding.attempted`).
It previously fired once per candidate on every generation run, including runs with
`tool_use` off that had never called anything.

## Entry points

- **`think(spec, investigator=None)`** — the orchestrator's callable interface. Pure: it
  neither writes a file nor logs to disk.
- **`run(...)`** — the dev-harness/standalone entry point. Same graph, streamed through a
  `GraphRunLog`. Reads a fixture idea and writes a run `.jsonl`; performs **no workspace
  write**, which is why the manifest declares no `workspace_field` and the log lands in
  `dev/runs/graph_logs/`.

```bash
python -m dev.run agent creative_thinker --fixture creative_thinker_generation.json
python -m dev.run agent creative_thinker --fixture creative_thinker_refinement.json
python -m dev.run agent creative_thinker --fixture creative_thinker_refinement_question.json
```

### Showcase examples

`MANIFEST["examples"]` declares ten ready-made runs. Each names a file in
`dev/fixtures/`, so the dev UI's **Examples** dropdown (which fills the form from
`/api/fixture/<name>`) and `--fixture <same file>` are the same run. **Every example
states a `task`.** They are ordered as a story — generate, then develop what generation
produced:

| # | Example | Shows |
|---|---|---|
| 1 | Generation — 4 stances on two gaps | breadth fan-out, dedup vs batch + workspace |
| 2 | Generation — steered by the researcher's own ideas | `user_input` notes, constraints, a rejected direction; chosen styles |
| 3 | Generation — budget cuts the fan-out | `truncated` with the limit named |
| 4 | Refinement — deep dive on what generation produced | continues #1; prior ground feeds the prompts |
| 5 | Refinement — `depth=1` | the chain-of-thought degeneration through the same code path |
| 6 | Refinement — the user's competing idea | skeptic stance against the idea's own mechanism; most likely to raise a split |
| 7 | **Refinement — a question, answered** | the task is a question; the run replies through `answer`, records the finding as a section, leaves the description and problem alone, and puts anything further in `proposed_changes` |
| 8 | Merge | two source ideas plus their conflicts; conflicts resolved or recorded, never smoothed |
| 9 | Refinement with `tool_use` | a branch waits for a fact; the real Investigator, wired at the boundary |
| 10 | Refinement on a live workspace record, persisted | `ws:<slug>/<idea_id>`, normalized on read, trace written back |

The refinement fixtures start from `idea_from_generation.json` — the candidate a user
accepted out of a generation run, still carrying that run's nodes. That is what makes the
accumulated graph visible across runs, and what the next run reads so it does not
re-tread those branches.

`run(..., investigator="web")` wires `investigator_agent.run` as the injected callable.
The wiring is in the harness entry point, not in the agent: the thinker imports nothing
from the Investigator and only ever sees `(question) -> {answer, sources}`. The
Investigator's own downloads land in `workspace/dev_creative_thinker/` — its writes, at
the boundary, not the thinker's.

## Getting the trace onto disk

The thinker returns the modified copy and stops there. **Something has to persist it or
`runs[]` never reaches the file** — that is `src.agents.idea_agent.apply_envelope`, the
orchestrator's step, implemented on its own:

```
idea                  → persist over the workspace record (or discard)
answer + questions    → handed back, batched into one message; never written
drafts + suggestions  → node/edge creation ONLY after an explicit accept
status                → returned for the reflector
```

An accepted split mints the id, writes the typed edge carrying the rationale and the run
id, and appends the diverging branch's sub-graph onto the draft's inherited history. A
generated variant arrives with **no** proposal in front of it, so the intent recorded on
its own `origin.suggested` is what tells apply which edge to write — without that
fallback a generated variant lands in the graph as an orphan. A refused proposal is
marked `rejected` on the node that raised it, so it is not re-proposed next session.

In the harness this is opt-in: `run(..., persist="<slug>")`, or the `persist` field in
the dev UI (a `post_run` hook, so the write happens after the graph has finished, never
inside it). `idea="ws:<slug>/<idea_id>"` reads a live record — Phase-1 records are
normalized on read by `src/common/schemas/idea_compat.py`, which is how an idea with no
thinking graph gets its first one.

## Reviewing it (dev demo)

After any creative-thinker run the harness shows a **Result & suggestions** panel: the
run's candidates as the tree they were produced in, each promotable one with a checkbox;
the suggested operations as cards with an accept toggle; the questions; and the status
with its limit. Ticking `n` candidates and pressing *write to knowledge base* creates `n`
separate ideas, each with its own id, its own `addresses` edges and a carved copy of the
branch that produced it. Everything left unticked is recorded as `rejected` on the node
that raised it.

This is the **user's** half of the interaction, mocked up for demos — not the
orchestrator. There is no planner, no reflector and no batching of questions into a
message; the only thing running underneath is `apply_envelope`, gated on the ticks.
`POST /api/idea/apply` carries the decision.

## Viewing it

The dev harness has an **Idea** panel: the *idea graph* (accepted ideas + typed edges,
`addresses` to gaps) beside the *thinking tree* (each run's nodes as a band under its
header, colour by status, style badge, `show pruned` toggle, click-to-inspect,
truncation marked). The two are deliberately not merged — the tree never leaks into the
graph panel.

## Known deviations from the design notes

- **Dedup is lexical, not semantic.** The notes call for embedding-based dedup against
  the batch *and* the stored idea graph ("the one genuinely new component you need");
  this is Jaccard over token sets (`tools/text.py`). It catches restatements, not
  differently-worded duplicates. The seam is one function.
- **No beam.** The notes specify tree search with beam ≈ 3 and soft pruning that
  "reserves one beam slot for the highest-variance branch"; the selector keeps a single
  winner per round. Losers are recorded, not deleted, so nothing is lost from the trace —
  but a good branch cut at depth 1 does not come back. This is now a *decision* rather
  than a gap: the one-call selector returns one winner or `undecided`, and an undecided
  round hands the whole frontier to the user rather than carrying it forward silently.
- **No ranking or scoring anywhere.** Generation used to order its batch by
  distinctness × grounded rating, and every kept branch used to carry a score (a
  constant 1.0, since a round keeps exactly one). Both are gone: a node records its
  `status` and the `comparison` line behind it, nothing numeric. The notes' MMR-style
  clustering — keep the best per cluster — is therefore not implemented either; dedup
  drops restatements and every survivor is returned.
- **No expansion cache.** The notes' reproducibility guard (cache expansions by state
  hash, log seeds) is not implemented; the stored tree is the record, but a re-run will
  not reproduce it.

## Not built here

- The **orchestrator** (`plan → execute → reflect`) — the sole writer and sole voice. It
  persists or discards `idea`, gates `drafts`/`suggested_operations` behind an explicit
  user accept, phrases `answer` and batches `questions` into one message, feeds `status`
  to the reflector, and on an accepted split mints the node and writes the typed edge.
- The **session layer** — the isolated `sessions/<session_id>/` folder holding the
  transcript, the loaded-context list and the **working copy** an unaccepted run
  iterates on. The thinker is pure and takes the copy as an argument; who holds it
  between calls is the session's business, and that folder does not exist yet.
- The **Investigator** — injected as a `(question) -> {answer, sources}` callable
  (`tools/investigate`). Nothing is imported from it.
- The dev-app **tree view**. Runs are recorded and replayable today; rendering them as a
  tree is a separate surface.
