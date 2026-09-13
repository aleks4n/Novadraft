# Ideation UI

The researcher-facing **Research Ideation** product UI — a web app and a desktop
app from one codebase. It renders the persistent knowledge base
(`workspace/<slug>/…`) using the *Research Ideation v2* design, and drives the
**paper agent** as a live, streamed run.

Phase 1 (paper agent: papers, gaps, summaries, topic switching) and the first
integrated Phase 2 flow (orchestrator, investigator, creative thinker, ideas, and sessions) are wired
to real data.

```
app/
  backend/     FastAPI — maps workspace JSON → the design data model + paper-agent SSE
  frontend/    Vite + React + TypeScript (the design, ported), wrapped by Tauri v2
    src-tauri/ desktop shell
```

## Stack

- **Web app:** Vite + React 18 + TypeScript SPA (`frontend/`).
- **Desktop app:** Tauri v2 (`frontend/src-tauri/`) loading the same build.
- **Backend:** FastAPI (`backend/`) driving the agents directly
  (`src.common.registry`, `src.common.utils.logging.runlog.RunRecorder`,
  `src.common.utils.storage`).

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

**1 — Backend** (from the `novadraft` root, so `src.*`/`app.*` resolve):

```bash
cd novadraft
.venv/bin/python -m uvicorn app.backend.main:app --reload --port 8787
```

Smoke-test it:

```bash
curl localhost:8787/api/topics
curl localhost:8787/api/topics/transformers/papers
curl localhost:8787/api/topics/transformers/gaps
```

**2 — Web app:**

```bash
cd novadraft/app/frontend
npm install        # first time
npm run dev        # http://localhost:5173  (proxies /api → :8787)
```

**3 — Desktop app** (optional; needs Rust + Xcode Command Line Tools):

```bash
cd novadraft/app/frontend
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

## What's live (Phase 2 — idea agent)

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
  column of windows you add, remove and resize (graph · loaded context · the agent
  plan · an idea page · a run trace). Context enters by clicking a graph node, dragging from the
  nav tree, pasting an id, dropping a file, or the composer's **+** search. It
  stages above the composer until you send, then joins the session context — lit
  in the graph and listed in the Loaded Context window.
- **A turn is planned by the orchestrator.** It reads the message and loaded
  context, then answers directly or calls the investigator and creative thinker
  with the mode, width, depth and styles it decides are appropriate. The reply and
  every proposed idea render together in the transcript.
- **The plan is shown before it runs.** A turn that would call a subagent stops at
  its plan: the chat lists each step with the agent it calls *and the arguments it
  calls it with*, every one of them editable in place (the creative thinker's width
  and depth above all), and nothing is dispatched until you press **Start** —
  which runs a second run with the plan as you left it. **Discard** turns it down.
  The *Agent plan* window carries the same plan read-only, with a live status per
  step while the run goes. The editor's fields come from the tools' own argument
  models, so it cannot offer a value the tool would reject.
- **Accept / dismiss.** Everything a run produces lands as a **working copy** —
  a real `idea.json` in `sessions/<id>/drafts/`, not in the knowledge base.
  *Save as an idea* mints an id and writes it; *Accept into the idea* overwrites
  the record the run modified, carrying the run into its `runs[]`; *Dismiss*
  records the decision without writing globally. Deleting a session takes its
  working copies with it.

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
| POST | `…/sessions/{id}/send` | one chat turn → an orchestrator run |
| POST | `…/sessions/{id}/land` | write the reply onto the transcript + file all offered working copies |
| GET | `/api/agents/orchestrator/tools` | what the orchestrator can call and the arguments each call takes (the plan editor's fields) |
| POST | `…/sessions/{id}/plan/approve` | start a proposed plan, with the arguments the user edited |
| POST | `…/sessions/{id}/plan/cancel` | turn a proposed plan down; nothing runs |
| GET | `…/sessions/{id}/drafts` | the session's unaccepted working copies |
| POST | `…/sessions/{id}/drafts/{id}/accept` | write one into the global workspace |
| DELETE | `…/sessions/{id}/drafts/{id}` | dismiss one and record the decision in the session |
