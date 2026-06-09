# Multi-Agent Research System — Repository Structure

This document explains how the codebase is organized and, more importantly, the
rules for deciding **where any new piece of code belongs**. Read the rules
section before adding files — the folder tree is the easy part; placement is what
keeps the repo clean over time.

## Architecture in one paragraph

The system is a **modular sequential workflow**, not a central orchestrator
delegating to agents. Steps run in a defined order, where each step is either an
**LLM agent** or a **deterministic function**. The workflow is defined as a
[LangGraph](https://langchain-ai.github.io/langgraph/) graph (a linear graph is
still a valid graph), which gives us persistence, resumability, and step-level
tracing for free. We use **LangChain** for model access (`init_chat_model`), tool
binding, and message handling.

## The three kinds of code

Every piece of code is one of three things. This is the core mental model — in
LangGraph everything in the workflow is a "node," and "agent," "step," and "tool"
are just flavors of node.

| Kind | What it is | Where it lives |
|------|-----------|----------------|
| **Agent** | An LLM-driven step | `agent.py` in the agent's folder |
| **Step (node)** | A deterministic function the workflow always runs (e.g. RRF rerank) | `nodes.py` in the owning agent's folder |
| **Tool** | A function an **LLM decides to call** (bound via `.bind_tools()`) | `tools/` in the owning agent's folder |

A "step" and a "tool" are not the same thing. A tool is invoked by an LLM at
runtime; a step is run unconditionally by the workflow. The RRF "top 15 of 45"
ranking is a **step**, not a tool — so it goes in `nodes.py`, never in `tools/`.

## The two placement rules

These two rules decide where anything goes. Apply them in order.

**Rule 1 — Location follows *who uses it*.**

> One agent uses it → it lives in that agent's folder.
> Two or more agents use it → it graduates to `common/`.

**Rule 2 — Sub-folder follows *what kind it is*.**

> LLM-callable → `tools/`
> Deterministic function → `nodes.py` (agent-local) or `common/utils/` (shared)

**Worked examples:**

- **RRF rerank** (used only by the Validator) → `validator_agent/nodes.py`
- **Download paper** (used by Survey & Gap *and* the Validator's deep-read) →
  `common/` (deterministic, so `common/tools/papers.py` or `common/utils/`)

**Promote late, not early.** Move something into `common/` only the moment a
*second* real caller appears — not because it "sounds generic." A premature
`common/` becomes a junk drawer that everything depends on and nobody can safely
change. Promoting later is a one-line move; untangling an over-eager `common/` is
not. When unsure, keep it local.

## Folder layout

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

## Guardrails

**Don't over-nest.** A sub-agent earns a folder only if it has its own tools or
prompts. A sub-agent that's just one LLM call (e.g. Paper Summary) is a single
`agent.py` file, not a folder tree. Folders follow complexity, not hierarchy.

**Dependencies point upward only.** Agents import from `common/`, never from each
other and never from a sibling's `tools/` or `nodes.py`. If `paper_agent` needs
something from `validator_agent`, that something belongs in `common/`. This one
constraint is what makes each agent folder genuinely self-contained — you can lift
the whole folder out and reason about it in isolation.

**Folders mirror the graph.** `graph/build_graph.py` wires the main steps; each
main agent's `agent.py` wires its own sub-steps. When the folder nesting matches
the LangGraph subgraph nesting, the repo reads like the architecture diagram.

**Subgraphs can own their state.** A main agent with children (like
`survey_gap_agent`) is itself a LangGraph subgraph and may keep local state
separate from `graph/state.py`. Decide per agent whether it needs private state or
just reads/writes the shared one — it controls how isolated that sub-pipeline is.

## Quick decision flow

When adding new code, ask in order:

1. **Is it an LLM step, a deterministic step, or an LLM-callable tool?**
   → agent / `nodes.py` / `tools/`
2. **How many agents use it?** One → that agent's folder. Two+ → `common/`.
3. **Does this sub-agent have its own tools/prompts?** No → single file. Yes →
   its own folder under `subagents/`.
```
