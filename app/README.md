# Ideation UI

The researcher-facing **Research Ideation** product UI — a web app and a desktop
app from one codebase. It renders the persistent knowledge base
(`workspace/<slug>/…`) using the *Research Ideation v2* design, and drives the
**paper agent** as a live, streamed run.

This is separate from the internal `dev/` harness (a debug tool). Phase-1 (paper
agent: papers, gaps, summaries, topic switching) is wired to real data. Phase 2 is
wired to the **creative thinker** — ideas, the idea graph, and ideation sessions —
with the orchestrator around it still missing, which the session chat says out
loud rather than faking.

```
app/
  backend/     FastAPI — maps workspace JSON → the design data model + paper-agent SSE
  frontend/    Vite + React + TypeScript (the design, ported), wrapped by Tauri v2
    src-tauri/ desktop shell
```

## Stack

- **Web app:** Vite + React 18 + TypeScript SPA (`frontend/`).
- **Desktop app:** Tauri v2 (`frontend/src-tauri/`) loading the same build.
- **Backend:** FastAPI (`backend/`) reusing the dev-harness internals
  (`dev.registry`, `dev.runlog.RunRecorder`, `src.common.utils.storage`).

## Run it

**Quickstart — one command** (backend + web frontend together):

```bash
./app/start.sh    # from the novadraft root (or ./start.sh from inside app/)
```

Installs frontend deps on first run, starts the backend on :8787 and the web
app on :5173, and tears both down on Ctrl-C. This covers the web app only —
for the desktop shell, see step 3 below.

Or run each piece manually — useful for the desktop app, or when you want
backend/frontend logs in separate terminals. Three pieces; the backend is
shared by both the web and desktop apps.

**1 — Backend** (from the `code/novadraft` root, so `dev.*`/`src.*` resolve):

```bash
cd code/novadraft
python -m uvicorn app.backend.main:app --reload --port 8787
# `_env_bootstrap` re-execs into the novadraft conda env automatically.
```

Smoke-test it:

```bash
curl localhost:8787/api/topics
curl localhost:8787/api/topics/transformers/papers
curl localhost:8787/api/topics/transformers/gaps
```

**2 — Web app:**

```bash
cd code/novadraft/app/frontend
npm install        # first time
npm run dev        # http://localhost:5173  (proxies /api → :8787)
```

**3 — Desktop app** (optional; needs Rust + Xcode Command Line Tools):

```bash
cd code/novadraft/app/frontend
npm run tauri dev    # dev: opens a window on the Vite server (proxies /api)
npm run tauri build  # bundle: → src-tauri/target/release/bundle/{macos/*.app, dmg/*.dmg}
```

The desktop window loads the same frontend and talks to the backend on :8787, so
**start the backend first**. In `dev` the Vite proxy forwards `/api`; the
**bundled** app loads from Tauri's asset origin, so it calls the backend at an
absolute URL (`http://127.0.0.1:8787` — see `inTauri` in `src/lib/api.ts`; the
backend allows CORS). A future milestone bundles the Python backend as a Tauri
sidecar so the desktop app is self-contained.

## What's live (Phase 1 — paper agent)

- **Topic switching** — the sidebar picker lists real workspaces (`meta.json`).
- **Papers** — list + detail (Paper / Survey tabs) from `papers/<id>/paper.json`
  (metadata, `context_summary`, contributions, stated limitations).
- **Gaps** — list + detail from `gaps/pending/*.json`, with severity derived from
  gap type, the source-paper link, and the verbatim evidence quote.
- **Add paper → live run** — submit an arXiv ID / URL / DOI / title; the paper
  agent (`fetch → extract → summarize → gap`) runs and streams each step inline
  in the Add-paper step. On completion the KB refreshes and the new paper opens.

For paper work the AI lives inside the workflow steps rather than in a sidebar —
run progress shows where the work happens. Ideation is the exception, and
deliberately so: it is a conversation, so it has its own surface (below).
Collaborators / History remain out of scope.

## What's live (Phase 2 — creative thinker)

- **Ideas** — list, and an **idea page** with two tabs. *Content* is the record as
  a researcher reads and edits it: the spine (one-liner, description, problem)
  plus each open section as a collapsible block, editable in place. *Thinking* is
  the decision-graph window — every recorded run end to end, nodes coloured by
  status and badged by style, with truncation marked.
- **Idea graph** — the sidebar's *Research Ideas* header. Gaps on top, the ideas
  attacking them below, `addresses` plus the typed idea→idea relations, each edge
  carrying its rationale. Clicking a node opens its page.
- **Sessions** — one chat-like surface per line of work, stored under
  `workspace/<slug>/sessions/`. The chat is always on the left; the right is a
  column of windows you add, remove and resize (graph · loaded context · an idea
  page · a run trace). Context enters by clicking a graph node, dragging from the
  nav tree, pasting an id, dropping a file, or the composer's **+** search. It
  stages above the composer until you send, then joins the session context — lit
  in the graph and listed in the Loaded Context window.
- **A turn is one creative-thinker call.** The message is the run's `task`, the
  loaded context is its `context`, and the composer exposes the thinker's own
  knobs (mode, width, depth, styles, tool use). The result renders as what it is —
  proposals: the answer, the typed operations, open questions, drafts, and the
  modified idea copy shown as an idea page **with every changed part marked**.
- **Accept / dismiss.** Everything a run produces lands as a **working copy** —
  a real `idea.json` in `sessions/<id>/drafts/`, not in the knowledge base.
  *Save as an idea* mints an id and writes it; *Accept into the idea* overwrites
  the record the run modified, carrying the run into its `runs[]`; *Dismiss*
  deletes the file. Deleting a session takes its unaccepted copies with it.

## Backend ↔ design mapping

`backend/mappers.py` (pure functions) bridges the stored schemas
(`src/common/schemas/{summary,gap}.py`) to the design's `topic → {papers, gaps}`
model. Scores the agents don't produce yet (relevance / novelty / feasibility) are
returned `null` and the UI hides those widgets rather than inventing values.

## API

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/topics` | all workspaces as topics (+ counts) |
| GET | `/api/topics/{slug}` | one topic with papers + gaps inlined |
| GET | `/api/topics/{slug}/papers` | mapped papers (light) |
| GET | `/api/topics/{slug}/papers/{id}` | one paper (full: abstract, survey) |
| GET | `/api/topics/{slug}/gaps` | mapped gaps |
| POST | `/api/topics/{slug}/papers/run` | run the paper agent, **SSE** stream |
| PATCH | `/api/topics/{slug}/ideas/{id}` | hand-edit an idea's spine / sections (never `runs[]`) |
| GET/POST | `/api/topics/{slug}/sessions` | list / create ideation sessions |
| GET/PATCH/DELETE | `/api/topics/{slug}/sessions/{id}` | one session (transcript + loaded context) |
| POST | `/api/topics/{slug}/sessions/{id}/context` | load items into the session context |
| DELETE | `…/sessions/{id}/context/{kind}/{id}` | unload one item |
| POST | `…/sessions/{id}/files` | attach a dropped document as context |
| POST | `…/sessions/{id}/send` | one chat turn → a creative-thinker run |
| POST | `…/sessions/{id}/land` | write a run's envelope onto the transcript + file its working copies |
| GET | `…/sessions/{id}/drafts` | the session's unaccepted working copies |
| POST | `…/sessions/{id}/drafts/{id}/accept` | write one into the global workspace |
| DELETE | `…/sessions/{id}/drafts/{id}` | dismiss one (the file is deleted) |
