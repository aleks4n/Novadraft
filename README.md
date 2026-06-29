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
├── llm/
│   └── registry.py         # role→model map + chat helpers (the only LLM access point)
│
├── common/                 # used by 2+ agents — promote here when a second real caller appears
│   ├── tools/              # shared deterministic helpers (paper_fetch, paper_extract)
│   ├── schemas/            # shared Pydantic types passed across the pipeline
│   │   ├── paper.py        # PaperRecord
│   │   ├── summary.py      # PaperSummary, PaperSummaryPatch
│   │   ├── summary_verify.py  # SummaryCritique
│   │   ├── gap.py          # GapRecord
│   │   └── gap_verify.py
│   └── utils/
│       ├── storage.py      # workspace paths + paper.json read/write (the only path-builder)
│       ├── prompts.py      # load_prompt(__file__) → reads a sibling prompt.md
│       └── trace.py        # emit() / log_event() — LangGraph custom-stream tracing
│
└── agents/
    ├── paper_agent/                # main agent with subagents
    │   ├── agent.py                # build_paper_graph() + run() entrypoint
    │   ├── state.py                # PaperState (TypedDict, total=False)
    │   ├── SPEC.md                 # living doc: this agent + its subagents
    │   └── subagents/
    │       ├── summarize/          # subgraph: extractor↔critic cycle
    │       │   ├── agent.py        # build_summarize_graph() + summarize() entrypoint
    │       │   ├── state.py        # SummarizeState
    │       │   ├── tools/          # deterministic helpers only this subagent uses
    │       │   │   ├── sections.py
    │       │   │   └── grounding.py
    │       │   └── subagents/
    │       │       ├── extractor/  # leaf LLM subagent
    │       │       │   ├── agent.py    # run_extract()
    │       │       │   └── prompt.md
    │       │       └── critic/
    │       │           ├── agent.py    # run_critic()
    │       │           └── prompt.md
    │       └── gap/                # same shape as summarize/
    │           ├── agent.py
    │           ├── state.py
    │           ├── tools/
    │           └── subagents/{extractor,critic}/
    │
    └── validator_agent/            # linear LangGraph graph (stub — in progress)
        ├── agent.py
        ├── state.py
        ├── schemas.py              # types only this agent uses
        ├── SPEC.md
        ├── steps/                  # deterministic stages (search, rrf)
        └── subagents/
            ├── query_gen/{agent.py, prompt.md}
            ├── screen/{agent.py, prompt.md}
            ├── deep_read/{agent.py, prompt.md}
            └── verdict/{agent.py, prompt.md}
```

---

## Anatomy of an agent

An **agent** is a folder named after itself. There are two kinds:

- **Main agent** — has `agent.py`, `state.py`, `SPEC.md`, and a `subagents/`
  folder. `paper_agent` is the canonical example.
- **Leaf subagent** — one LLM call. Has `agent.py` + `prompt.md`, no `SPEC.md`.
  May nest its own `subagents/` if it is itself a small pipeline (like
  `summarize/` and `gap/`), in which case it also gets a `state.py` and
  `tools/`.

### `agent.py`

Every `agent.py` exposes two things:

1. **`build_<name>_graph()`** — returns a compiled `StateGraph`. Nodes are
   either deterministic tool calls or subgraph nodes (mounted via
   `g.add_node(name, build_subgraph())`). This makes subgraph steps first-class
   nodes: `xray` expands them in diagrams, and `stream(subgraphs=True)` nests
   their internal steps under their parent node automatically.

2. **A standalone entrypoint** (`run(...)` / `summarize(...)`) — compiles the
   graph once (cached in a module-level `_GRAPH`), invokes it, and returns the
   result dict. This is what the dev harness and eval pipeline import directly.

```python
# paper_agent/agent.py — minimal skeleton
def build_paper_graph():
    g = StateGraph(PaperState)
    g.add_node("fetch",     fetch_node)
    g.add_node("extract",   extract_node)
    g.add_node("summarize", build_summarize_graph())   # subgraph as node
    g.add_node("gap",       build_gap_graph())         # subgraph as node
    g.add_node("assemble",  assemble)
    g.add_edge(START, "fetch")
    g.add_conditional_edges("fetch", _route_after_fetch, {"extract": "extract", END: END})
    g.add_edge("extract", "summarize")
    g.add_edge("summarize", "gap")
    g.add_edge("gap", "assemble")
    g.add_edge("assemble", END)
    return g.compile()

_GRAPH = None
def run(category_slug: str, query: str, force: bool = False) -> dict:
    global _GRAPH
    if _GRAPH is None:
        _GRAPH = build_paper_graph()
    final = _GRAPH.invoke({"category_slug": category_slug, "query": query, "force": force},
                          config={"recursion_limit": 60})
    return final["result"]
```

### `state.py`

Always a `TypedDict(total=False)`. `total=False` means nodes return only the
keys they set — you never have to return the full state from a node.

Group keys into **inputs**, **per-stage outputs**, and **control / final
output**. When a subgraph is mounted as a node inside a parent graph, its
state shares some key names with the parent. Document which keys are shared
(inputs + the single output key) and which are subgraph-private:

```python
# summarize/state.py
class SummarizeState(TypedDict, total=False):
    # inputs — shared with PaperState (flow in from the parent graph)
    category_slug: str
    paper_id: str
    force: bool

    # loop-carry — subgraph-private (never touch PaperState)
    attempt: int
    feedback: List[dict] | None
    done: bool
    ...
    summarize_extract: dict   # NOT "extract_result" — that name belongs to PaperState

    # output — shared key (written back into PaperState)
    summary_result: dict
```

### `subagents/`

Each leaf subagent is its own folder:

```
subagents/
└── extractor/
    ├── __init__.py
    ├── agent.py       # exposes run_extract(category_slug, paper_id, ...)
    └── prompt.md      # the LLM's system prompt
```

`agent.py` is a **pure pass** — no graph/loop knowledge. It reads from
`paper.json`, builds the user prompt, calls the LLM through the registry,
validates/writes the result, and returns a result dict. All tracing is
automatic (see below).

```python
# extractor/agent.py — skeleton
SYSTEM_PROMPT = load_prompt(__file__)      # reads sibling prompt.md

def run_extract(category_slug, paper_id, force=False, feedback=None) -> dict:
    record = storage.read_paper_json(category_slug, paper_id)
    ...
    user_prompt = build_user_prompt(...)
    parsed = chat_with_structured_output(get_llm("reasoner"), SYSTEM_PROMPT, user_prompt, PaperSummary)
    ...
```

### `tools/`

Everything deterministic. Plain Python functions — no LLM calls, no graphs.

- **Agent-local**: `<agent>/tools/` — only that agent uses them.
- **Shared**: `src/common/tools/` — promote here the moment a second real
  caller appears (Rule 1). Never pre-promote "because it sounds generic."

### `schemas/`

Pydantic models used as the `response_schema` passed to `chat_with_structured_output`.
The LLM is forced via function-calling to emit exactly that shape.

- **Shared schemas** (read or written by more than one agent) live in
  `src/common/schemas/`: `PaperSummary`, `SummaryCritique`, `GapRecord`, etc.
- **Agent-local schemas** (only one agent uses them) sit next to that agent,
  e.g. `validator_agent/schemas.py`.

A schema can optionally expose a `prompt_schema()` classmethod that returns a
human-readable field description to append to the user prompt — this keeps the
schema definition and the prompt description in sync.

### `prompt.md`

Each leaf subagent keeps its system prompt in a sibling `prompt.md`. Load it
with:

```python
from src.common.utils.prompts import load_prompt
SYSTEM_PROMPT = load_prompt(__file__)
```

`load_prompt(__file__)` reads the `prompt.md` in the same directory as the
calling module. Pass `**format_args` to fill in `{placeholders}`.

### `SPEC.md`

Main agents only. A living document describing the pipeline stages, what each
node does, and how subagents interact. Keep it up to date with code changes.

---

## LLM registry (`src/llm/registry.py`)

**All LLM access goes through here.** No agent calls a chat model directly.

### Why

- Every call through the registry automatically emits an `llm` trace event
  (see Logging below), so prompts/responses appear in the dev UI's run tree
  under the correct node with **zero tracing code in the agent**.
- Provider fallback (`reasoner` → `cheap` on transient failure) is wired here
  once, not repeated in every agent.
- The offline mock (`_MockChat`) lives here — dev runs work without an API key.

### Roles

```python
MODELS = {
    "reasoner": "deepseek:deepseek-reasoner",   # thinking model, best quality
    "cheap":    "deepseek:deepseek-chat",        # fast/cheap, used as fallback
}
```

### Functions

**`get_llm(role, temperature=0.2)`** — returns a LangChain chat model. The
`"reasoner"` role is wrapped with an automatic fallback to `"cheap"`, so a
transient reasoner failure retries on the cheap model. When no API key is set,
returns a `_MockChat` (deterministic offline stand-in that returns empty
responses without hitting the network).

**`chat(llm, system, user) → str`** — one call on `llm`. Returns the raw
response text. Use this when you don't need structured output (e.g. freeform
generation, summarization without a schema).

**`chat_with_structured_output(llm, system, user, schema) → schema_instance`**
— one call on `llm` that forces function-calling to return exactly `schema` (a
Pydantic model class). Returns the validated instance — no text parsing or
regex needed. Use this for any LLM call that must produce a typed result.

```python
from src.llm.registry import chat_with_structured_output, get_llm
from src.common.schemas.summary import PaperSummary

llm = get_llm("reasoner")
result: PaperSummary = chat_with_structured_output(llm, SYSTEM_PROMPT, user_prompt, PaperSummary)
```

Both functions call `_trace()` internally, which calls `emit()` in
`src/common/utils/trace.py` — the LLM call becomes a leaf in the run tree
under whichever graph node called the registry.

---

## Logging (`src/common/utils/trace.py`)

There is no separate logging system. Progress **is** the LangGraph stream.
Graphs are run with:

```python
graph.stream(inputs, stream_mode=["updates", "custom"], subgraphs=True)
```

- **`updates`** — after each node completes, a `{node: state_delta}` chunk.
  Each node is one **step** in the run tree; its delta is the step's result.
- **`custom`** — fine-grained leaves emitted from inside a node via
  `trace.emit(...)` or `trace.log_event(...)`.
- **`subgraphs=True`** — carries a namespace so a subgraph node and its
  internal steps nest under their parent node.

### When to call what

**`emit(event: dict)`** — emit any structured leaf into the custom stream.
Use this directly when you want to emit a typed event with a specific structure.
The registry calls it automatically for every LLM call and tool result, so you
typically only call `emit` yourself for domain-specific events that need custom
`data` fields.

```python
from src.common.utils.trace import emit as emit_trace

emit_trace({"type": "event",  "name": "no progress since last round, stopping",
            "data": {"attempt": attempt, "n_unmatched": len(unmatched)}})

emit_trace({"type": "tool",   "name": "grounding",
            "data": {"checked": grounding["checked"], "unmatched": grounding["unmatched"]}})
```

**`log_event(message: str, **data)`** — convenience wrapper over `emit` for
simple informational notes. The equivalent of a `print` that ends up in the
run tree. Use this in deterministic tool functions that want to narrate progress.

```python
from src.common.utils.trace import log_event

log_event("already summarized, skipping", paper_id=paper_id)
# equivalent: emit({"type": "event", "name": "already summarized, skipping",
#                   "data": {"paper_id": paper_id}})
```

### Event types

| `type`  | When to use                                                   |
|---------|---------------------------------------------------------------|
| `"llm"` | LLM call. Emitted automatically by the registry — don't emit manually. |
| `"tool"`| Deterministic tool call with inputs/outputs worth surfacing.  |
| `"event"` | Any informational note: skipping, short-circuiting, loop decisions. |

### Outside a running graph

Both `emit` and `log_event` are **no-ops** when called outside a running graph
(standalone subagent calls, the CLI, the eval harness, tests). No guard needed
— the same code path is safe everywhere.

---

## Workspace layout

```
workspace/<category_slug>/
├── meta.json              # name, description, created_at/updated_at
├── papers/
│   └── <paper_id>/
│       ├── paper.json     # PaperRecord: metadata, summary, gaps, processing flags
│       ├── source.pdf
│       └── source/...     # extracted tex tree, if available
├── gaps/
│   └── pending/
│       └── <gap_id>.json  # one GapRecord per extracted gap
└── logs/
    └── <agent>/
        └── <run_id>.json  # one finished run tree per top-level agent run
```

`paper_id` is derived deterministically from the input (`arxiv_<id>`,
`doi_<slug>`, or a title hash) so re-fetching the same paper is a no-op.
All paths are resolved through `src/common/utils/storage.py` — the only place
that constructs workspace paths.

---

## The dev harness

### What it's for

`dev/` is a thin CLI + small web UI for invoking agents, subagents, and
multi-agent flows directly without a frontend. Every invocation registered in
`dev/registry.py` (`AGENTS`, `SUBAGENTS`, `FLOWS`) is automatically runnable,
listable, and logged.

### How to call it

```bash
python -m dev.run list

python -m dev.run agent paper_agent \
    --kwargs '{"category_slug": "transformers", "query": "1706.03762"}'

python -m dev.run subagent gap_extract \
    --kwargs '{"category_slug": "transformers", "paper_id": "arxiv_1706.03762"}'

python -m dev.run subagent paper_fetch --fixture sample_paper.json
```

Every call writes a `dev/runs/<timestamp>_<name>.json` record regardless of
whether the agent also wrote a workspace log. New agents/subagents get
registered in `dev/registry.py`, not hardcoded into `dev/run.py`.

### Adding a new agent checklist

1. Build it as a `StateGraph` in `src/agents/<name>/agent.py` — expose
   `build_<name>_graph()` and a standalone `run(...)` entrypoint.
2. Add `state.py` with a `TypedDict(total=False)`.
3. Leaf subagents: `subagents/<name>/agent.py` + `prompt.md`. Call the LLM
   via `chat_with_structured_output(get_llm("reasoner"), ...)`.
4. Schemas: agent-local in `<agent>/schemas.py`; shared (2+ callers) in
   `src/common/schemas/`.
5. Register in `dev/registry.py` under `AGENTS` (or `SUBAGENTS`/`FLOWS`).
6. Write a `SPEC.md` for main agents.

### Evaluation scripts

```bash
python -m dev.eval_paper_pipeline --category transformers --label baseline
python -m dev.eval_paper_pipeline --category transformers --label baseline \
    --mode summary --queries "Attention Is All You Need" 1406.2661
python -m dev.eval_paper_pipeline --category transformers --label baseline \
    --mode gap --papers arxiv_1706.03762
```

`--mode` selects `summary`, `gap`, or `both`. Reports go to
`dev/eval_runs/<timestamp>_<mode>_<label>/` (`report.md` + `raw.json`).

---

## Setup

Copy `.env.example` to `.env` and add your keys:

```
DEEPSEEK_API_KEY=...   # primary provider (reasoner + cheap)
OPENAI_API_KEY=...     # optional fallback
```

When no key is set, `get_llm` returns a deterministic `_MockChat` and all
runs complete without network access (LLM responses are empty stubs).

---

## Further reference

- **Architecture decisions**: `planning/INDEX.md` (source of truth for design)
- **Logging system schema**: `planning/technical-discussions/LOGGING_SYSTEM.md`
- **Phase breakdown**: `planning/AGENT_ARCHITECTURE_PHASES.md`
- **Paper agent spec**: `src/agents/paper_agent/SPEC.md`
