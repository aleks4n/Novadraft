# novadraft — Research Ideation Workspace

## What this is

`novadraft` is the backend for a **co-living research ideation workspace**: a
persistent environment where a researcher and a set of LLM agents build up a
knowledge base of papers, gaps, and ideas for a research topic ("category")
over many sessions — not a one-shot pipeline. Each category is its own
workspace on disk; agents read and write into it incrementally as papers get
added and discussed.

---

## Repository structure

```
src/
├── graph/                  # the workflow definition (the sequence of steps)
│   ├── build_graph.py      # wires the main agents/steps together in order
│   ├── state.py            # the shared graph State schema (TypedDict/Pydantic)
│   └── routing.py          # any conditional edges between steps
│
├── llm/
│   └── registry.py         # role→model map via init_chat_model, fallbacks
│
├── common/                 # used by MANY agents (Rule 1: 2+ callers)
│   ├── tools/              # shared LLM-callable tools + shared I/O (download_paper…)
│   ├── schemas/            # shared types passed across the pipeline (Paper, Gap…)
│   └── utils/              # shared deterministic helpers (chunking, dedup, io)
│       └── runlog.py       # logging system for agents and steps - start_run / step / skip_step / log_event / log_call
│
└── agents/
    ├── paper_agent/
    │   ├── agent.py        # the LLM step
    │   ├── nodes.py        # deterministic in-between steps (if any)
    │   ├── prompts.py
    │   ├── schemas.py      # types ONLY this agent uses
    │   └── tools/          # tools ONLY this agent's LLM calls
    │
    ├── validator_agent/
    │   ├── agent.py        # wires the validator's internal sequence (subgraph)
    │   ├── nodes.py        # deterministic steps — RRF rerank (top 15 of 45) lives here
    │   ├── prompts.py
    │   ├── schemas.py
    │   └── tools/          # e.g. the query-LLM's search tool
    │
    ├── ranking_agent/
    │   └── agent.py
    │
    └── survey_gap_agent/           # a main agent that HAS sub-agents
        ├── agent.py                # the subgraph wiring its children in sequence
        ├── nodes.py
        ├── prompts.py
        ├── state.py                # sub-state, if this subgraph needs its own
        ├── tools/                  # tools shared by ITS sub-agents only
        └── subagents/
            ├── paper_fetcher/
            │   ├── agent.py
            │   └── tools/
            ├── paper_extractor/
            │   ├── agent.py
            │   └── tools/
            ├── paper_summary/
            │   └── agent.py        # no tools → just one file, no folder
            └── gap_agent/
                ├── agent.py
                └── tools/

```

---

## Workspace layout

Every category lives in its own folder under `workspace/`, identified by a
`category_slug` (e.g. `transformers`):

```
workspace/<category_slug>/
├── meta.json              # name, description, created_at/updated_at
├── papers/
│   └── <paper_id>/
│       ├── paper.json     # the PaperRecord: metadata, summary, gaps, processing flags
│       ├── source.pdf
│       └── source/...     # extracted tex tree, if available
├── gaps/
│   └── pending/
│       └── <gap_id>.json  # one GapRecord per extracted gap, keyed by id
└── logs/
    └── <agent>/
        └── <run_id>.json  # one file per top-level agent run (see Logging below)
```

`paper_id` is derived deterministically from the input (`arxiv_<id>`,
`doi_<slug>`, or a title hash) so re-fetching the same paper is a no-op
rather than a duplicate. All paths are resolved through
`src/common/utils/storage.py`, which is the only place that should
construct workspace paths.

---

## Code organization

### Agent vs. step

- **Agent**: an LLM-driven unit of work — it has a `prompt.py` and calls the
  LLM (directly or through a `chat_json*` helper) to produce a result.
- **Step**: what the *dev harness and the logger* call any one stage of a
  pipeline, whether that stage happens to be an agent or a plain
  deterministic function.

`paper_agent.run()` wires five steps — `fetch`, `extract`, `summary`,
`summary_verify`, `gaps` — and only two of them (`summary`/`paper_summary`,
`gaps`/`gap_agent`, and indirectly `summary_verify`) are themselves agents;
`fetch` and `extract` are plain deterministic tools. In other words: every
agent is wrapped as a step when it runs inside a pipeline, but not every step
is an agent.

### `common/`

`src/common/` holds anything used by **more than one** agent (Rule 1:
promote to `common/` the moment a *second* real caller appears, not because
something "sounds generic"). It's split by what kind of content it is:

- **`tools/`**: shared, LLM-callable tools and shared I/O — e.g. `paper_fetch`
  (resolves a query to a `paper_id` and downloads the source) and
  `paper_extract` (pulls text/tex out of the downloaded source). Both live
  here because `paper_agent` and the eval harness call them directly.
- **`schemas/`**: shared Pydantic types passed across the pipeline — e.g.
  `PaperRecord`, `PaperSummary`, `SummaryVerification`, `GapRecord`. These
  live in `common/` rather than next to one agent because the same record is
  read and written by several stages of the pipeline (fetch writes it,
  summary reads/extends it, gaps extend it again).
- **`utils/`**: shared deterministic helpers — `storage.py` (workspace paths
  + `paper.json` read/write) and `runlog.py` (the run-logging system used by
  every agent, subagent, and tool; see Logging below).

### Structure of a single agent

See `paper_agent/` in the tree above:

- **`agent.py`**: the entrypoint, e.g. `run(category_slug, query,
  force=False)` — wires the agent's steps/subagents in order.
- **`subagents/`**: one folder per pipeline stage that needs its own prompt
  or logic (e.g. `subagents/gap_agent/prompt.py` + `gap_extract.py` +
  `verify.py`). A subagent only earns a folder when it has its own prompt or
  supporting logic; a one-call subagent with nothing else would just be a
  single file.
- **schema**: each subagent's output is validated against a Pydantic model
  in `src/common/schemas/` (`PaperSummary`, `GapRecord`,
  `SummaryVerification`, ...), passed as `response_schema` to the LLM call so
  the model is forced via function-calling to emit exactly that shape (see
  `src/llm/registry.py` below).

LLM access for every agent goes through one place: `src/llm/registry.py`,
which maps a logical **role** (`"reasoner"`, `"cheap"`) to a concrete
provider model via LangChain's `init_chat_model`, and exposes two call
shapes:

- **`chat_json(system, user, role, response_schema)`**: one request/response.
  With a schema, it binds `with_structured_output(schema)` so the LLM is
  forced through function-calling to return exactly that Pydantic shape (no
  regex/text parsing). Without one, it loosely parses JSON out of the raw
  text (handles markdown fences).
- **`chat_json_with_tools(system, user, tools, tool_handlers,
  response_schema)`**: runs a LangGraph tool-calling loop (the LLM calls
  tools like "get a section of the paper" until it's ready), then makes one
  final structured-output call over the whole transcript to lock in the
  schema. Used by `gap_agent`, which needs to pull specific sections of the
  source text on demand rather than seeing the whole paper up front.

No agent calls a chat model directly — going through the registry is what
makes every prompt/response show up in the run log for free (see below), and
is also where provider fallback (`reasoner` → `cheap` on failure) and the
offline mock (`_MockChat`, used when no API key is set) live.

### Logging

Every agent, subagent, and tool function logs through one shared module:
`src/common/utils/runlog.py`. Three calls, usable identically anywhere:

```python
with start_run(category_slug, "paper_agent", inputs, run_hint=query) as run:
    with step("fetch") as node:
        result = fetch(category_slug, query)
        node["data"]["result"] = result
    log_event("found arxiv id, fetching pdf", url=url)
    run.output = {...}
```

- **`start_run(...)`** is called exactly once, by a *main* agent's
  entrypoint (`paper_agent.run`). It opens one JSON file per run at
  `workspace/<category_slug>/logs/<agent>/<run_id>.json` and flushes it to
  disk after every step/event, so it's tailable mid-run. This is the **only**
  per-run JSON log file — there's no separate "general" log; each agent's
  own run produces its own file, named by timestamp + a slug of the query.
- **`step(name, **data)`** wraps one pipeline stage and nests under whatever
  step is currently open. Called identically by main agents, subagents, and
  plain functions — they never know whether a run is active. If one is, the
  call is appended to that run's JSON file; if not (e.g. a subagent is being
  exercised standalone via the dev harness), it only prints to the terminal.
- **`log_event(...)`** is a fine-grained note inside the current step —
  unlike `step`, it's *always* written to the active run's log (never
  silently dropped) and always printed.
- **`log_call(...)`** records an already-finished leaf call (one LLM
  round-trip, one tool invocation). Agents don't call this directly —
  `src/llm/registry.py` calls it internally on every `chat_json*` call, so
  every prompt and response shows up nested under whichever `step()` was
  open when the call happened, with zero logging code inside the agent
  itself.

Full on-disk schema and instructions for instrumenting a new agent:
`planning/technical-discussions/LOGGING_SYSTEM.md`.

---

## The dev harness

### What it's for

`dev/` is a thin CLI (and a small web UI, `dev/ui.py` +
`dev/static/index.html`) for invoking agents, subagents, and multi-agent
flows directly, without going through a frontend. It's the fast loop for
iterating on a single prompt or stage: run it, see the JSON output and the
step-by-step log, repeat. Every invocation registered in `dev/registry.py`
(`AGENTS`, `SUBAGENTS`, `FLOWS`) is automatically runnable, listable, and
loggable.

### How to call it

```bash
python -m dev.run list                                    # show registered agents/subagents/flows

python -m dev.run agent paper_agent \
    --kwargs '{"category_slug": "transformers", "query": "1706.03762"}'

python -m dev.run subagent gap_extract \
    --kwargs '{"category_slug": "transformers", "paper_id": "arxiv_1706.03762"}'

python -m dev.run subagent paper_fetch --fixture sample_paper.json   # --fixture loads dev/fixtures/<file>.json as kwargs
```

Every call also writes a `dev/runs/<timestamp>_<name>.json` record (inputs,
output, error, duration) regardless of whether the thing it called also
wrote a workspace run log — this is the harness's own invocation log, separate
from `workspace/<category>/logs/`. New agents/subagents/flows get registered
in `dev/registry.py`, not hardcoded into `dev/run.py`.

### Evaluation scripts

`dev/eval_paper_pipeline.py` re-runs `summarize()`/`extract_gaps()` with
`force=True` over a set of papers in a category, times each call, and writes
a timestamped report under `dev/eval_runs/<timestamp>_<mode>_<label>/`
(`report.md` for humans, `raw.json` as the diff target for comparing prompt
or model changes across runs):

```bash
# Evaluate every already-extracted paper in the workspace
python -m dev.eval_paper_pipeline --category transformers --label baseline

# Fetch+extract specific papers first (idempotent), then evaluate just those
python -m dev.eval_paper_pipeline --category transformers --label baseline \
    --mode summary --queries "Attention Is All You Need" 1406.2661

# Evaluate specific existing paper_ids without touching fetch/extract
python -m dev.eval_paper_pipeline --category transformers --label baseline \
    --mode gap --papers arxiv_1706.03762
```

`--mode` selects `summary`, `gap`, or `both`. The same functions
(`run_pipeline`, `write_report`, ...) are imported by `dev/ui.py` to drive
identical evals from the web UI with live progress callbacks.

---

## Suggestions for further additions

- **Setup section** — `.env` keys required (`DEEPSEEK_API_KEY` /
  `OPENAI_API_KEY`), and that the system falls back to a deterministic mock
  LLM when none is set (useful to call out explicitly so a new contributor
  doesn't wonder why everything returns `{}`).
- **`validator_agent` is currently an empty stub** (`src/agents/validator_agent/agent.py`
  is a 0-byte placeholder) — worth a one-line note so it's clear that's
  intentional/in-progress, not a bug.
- **A short "adding a new agent" checklist** (wrap entrypoint in
  `start_run`, wrap stages in `step`, register in `dev/registry.py`'s
  `AGENTS`/`SUBAGENTS`, add a schema if it returns structured output) — most
  of this is already in `LOGGING_SYSTEM.md`, just worth a pointer here.
- **Link to `planning/INDEX.md`** at the top so this README and the planning
  docs don't drift apart, since planning docs are the source of truth for
  architecture decisions per `CLAUDE.md`.
