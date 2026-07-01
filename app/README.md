# Ideation UI

The researcher-facing **Research Ideation** product UI — a web app and a desktop
app from one codebase. It renders the persistent knowledge base
(`workspace/<slug>/…`) using the *Research Ideation v2* design, and drives the
**paper agent** as a live, streamed run.

This is separate from the internal `dev/` harness (a debug tool). Phase-1 (paper
agent: papers, gaps, summaries, topic switching) is wired to real data; Ideas /
Phases 2–3 are present as placeholders.

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

Three pieces. The backend is shared by both the web and desktop apps.

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

The AI lives inside these workflow steps, not as a separate chat assistant — so
there is no standalone agent sidebar. Collaborators / History (and ideation in
Phases 2–3) are out of scope for now.

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
