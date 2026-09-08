// API client for the ideation backend. Reads are plain fetch; the paper-agent
// run is an SSE stream (the reader mirrors dev/static/index.html::runStreaming).

import type {
  AgentPlan, ChatMessage, ContextRef, Idea, Paper, RunEvent, RunMeta, Session,
  SessionDraft, SessionSummary, ToolSpec, TopicBundle, TopicMeta,
} from "./types";

// Web app: relative URLs, proxied to the backend by Vite (dev) / served same-
// origin (prod static). Desktop app: the bundle loads from Tauri's asset origin,
// which has no proxy, so call the backend directly (CORS is open on the backend).
// Override either with VITE_API_BASE at build time.
const inTauri =
  typeof window !== "undefined" &&
  ("__TAURI_INTERNALS__" in window || "__TAURI__" in window);
export const API_BASE =
  import.meta.env.VITE_API_BASE ?? (inTauri ? "http://127.0.0.1:8787" : "");

async function getJSON<T>(path: string): Promise<T> {
  const r = await fetch(API_BASE + path);
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.json() as Promise<T>;
}

export const api = {
  topics: () => getJSON<{ topics: TopicMeta[] }>("/api/topics").then((d) => d.topics),
  topic: (slug: string) => getJSON<TopicBundle>(`/api/topics/${encodeURIComponent(slug)}`),
  // Bootstrap a new category via the category agent (LLM description → write
  // meta.json). Resolves with the created topic once its meta.json is on disk.
  createTopic: (topic: string) => postJSON<TopicMeta>("/api/topics", { topic }),
  paper: (slug: string, id: string) =>
    getJSON<Paper>(`/api/topics/${encodeURIComponent(slug)}/papers/${encodeURIComponent(id)}`),
  ideas: (slug: string) =>
    getJSON<{ ideas: Idea[] }>(`/api/topics/${encodeURIComponent(slug)}/ideas`).then((d) => d.ideas),
  idea: (slug: string, id: string) =>
    getJSON<Idea>(`/api/topics/${encodeURIComponent(slug)}/ideas/${encodeURIComponent(id)}`),
};

// --- server-tracked runs ------------------------------------------------------
// Starting a run returns its meta immediately (the run executes detached on the
// backend and survives reloads / dropped connections). Progress is observed via
// GET /api/runs/{id}/stream, which replays the recorded events and follows live.

async function postJSON<T>(path: string, body?: unknown): Promise<T> {
  const r = await fetch(API_BASE + path, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.json() as Promise<T>;
}

async function patchJSON<T>(path: string, body: unknown): Promise<T> {
  const r = await fetch(API_BASE + path, {
    method: "PATCH",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.json() as Promise<T>;
}

async function del(path: string): Promise<void> {
  const r = await fetch(API_BASE + path, { method: "DELETE" });
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
}

async function delJSON<T>(path: string): Promise<T> {
  const r = await fetch(API_BASE + path, { method: "DELETE" });
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.json() as Promise<T>;
}

export function listRuns(slug: string): Promise<RunMeta[]> {
  return getJSON<{ runs: RunMeta[] }>(`/api/topics/${encodeURIComponent(slug)}/runs`)
    .then((d) => d.runs);
}

export const cancelRun = (runId: string) =>
  postJSON<RunMeta>(`/api/runs/${encodeURIComponent(runId)}/cancel`);
export const restartRun = (runId: string) =>
  postJSON<RunMeta>(`/api/runs/${encodeURIComponent(runId)}/restart`);
export async function deleteRun(runId: string): Promise<void> {
  const r = await fetch(`${API_BASE}/api/runs/${encodeURIComponent(runId)}`, { method: "DELETE" });
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
}

export interface StreamHandlers {
  onEvent?: (ev: RunEvent) => void;      // run_start / node / llm / event / run_end
  onMeta?: (meta: RunMeta | null) => void; // final server meta when the stream closes
}

// Follow a run's event stream from `since` until it reaches a terminal/paused
// state. Resolves when the stream closes (the run itself may live on paused).
export async function followRun(runId: string, since: number, handlers: StreamHandlers): Promise<void> {
  const resp = await fetch(
    `${API_BASE}/api/runs/${encodeURIComponent(runId)}/stream?since=${since}`);
  if (!resp.ok || !resp.body) throw new Error(`${resp.status} ${await resp.text()}`);

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let idx: number;
    while ((idx = buf.indexOf("\n\n")) >= 0) {
      const chunk = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      const line = chunk.split("\n").find((l) => l.startsWith("data: "));
      if (!line) continue;
      const evt = JSON.parse(line.slice(6));
      if (evt.type === "meta") handlers.onMeta?.(evt.run ?? null);
      else handlers.onEvent?.(evt.event);
    }
  }
}

// Attach a user-supplied full-text source (pdf or LaTeX archive) to a paper that
// fetched without one. Resolves with the updated paper.
export async function uploadPaperSource(slug: string, paperId: string, file: File): Promise<Paper> {
  const fd = new FormData();
  fd.append("file", file);
  const r = await fetch(
    `${API_BASE}/api/topics/${encodeURIComponent(slug)}/papers/${encodeURIComponent(paperId)}/source`,
    { method: "POST", body: fd },
  );
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.json() as Promise<Paper>;
}

export function runPaperAgent(slug: string, query: string, force = false): Promise<RunMeta> {
  return postJSON<RunMeta>(`/api/topics/${encodeURIComponent(slug)}/papers/run`, { query, force });
}

// Resume gap ingestion (director → validator) from where it stopped, using the
// gaps' stage folders as the checkpoint. Scope to one gap or one paper's gaps.
export function resumeGap(slug: string, gapId: string): Promise<RunMeta> {
  return postJSON<RunMeta>(
    `/api/topics/${encodeURIComponent(slug)}/gaps/${encodeURIComponent(gapId)}/resume`, {});
}

export function resumePaperGaps(slug: string, paperId: string): Promise<RunMeta> {
  return postJSON<RunMeta>(
    `/api/topics/${encodeURIComponent(slug)}/papers/${encodeURIComponent(paperId)}/resume-gaps`, {});
}

export function runIdeaAgent(
  slug: string,
  body: { mode: "generate" | "expand"; sketch?: string; count?: number },
): Promise<RunMeta> {
  return postJSON<RunMeta>(`/api/topics/${encodeURIComponent(slug)}/ideas/run`, body);
}


// --- sessions ------------------------------------------------------------------
// The session chat surface. Sessions are workspace records, so everything here is
// a plain read/write against workspace/<slug>/sessions/ — only `send` starts a run.

const S = (slug: string) => `/api/topics/${encodeURIComponent(slug)}/sessions`;

export const sessionApi = {
  list: (slug: string) =>
    getJSON<{ sessions: SessionSummary[] }>(S(slug)).then((d) => d.sessions),
  get: (slug: string, id: string) =>
    getJSON<Session>(`${S(slug)}/${encodeURIComponent(id)}`),
  create: (slug: string, title = "", context: { kind: string; id: string }[] = []) =>
    postJSON<Session>(S(slug), { title, context }),
  rename: (slug: string, id: string, title: string) =>
    patchJSON<Session>(`${S(slug)}/${encodeURIComponent(id)}`, { title }),
  remove: (slug: string, id: string) => del(`${S(slug)}/${encodeURIComponent(id)}`),

  // Load items into the session context without sending a turn — the
  // click-in-the-panel path. Items already loaded are a no-op, not a duplicate.
  addContext: (slug: string, id: string, context: { kind: string; id: string }[]) =>
    postJSON<{ session: Session; added: ContextRef[] }>(
      `${S(slug)}/${encodeURIComponent(id)}/context`, { context }),
  removeContext: (slug: string, id: string, kind: string, itemId: string) =>
    del(`${S(slug)}/${encodeURIComponent(id)}/context/${encodeURIComponent(kind)}/${encodeURIComponent(itemId)}`),

  // One chat turn: records the message, loads its context, and starts the idea
  // orchestrator (or the direct thinker when explicitly requested).
  send: (slug: string, id: string, body: SendBody) =>
    postJSON<{ session: Session; message: unknown; run: RunMeta }>(
      `${S(slug)}/${encodeURIComponent(id)}/send`, body),

  // Write a finished run's envelope onto the transcript and file its unaccepted
  // output as working copies. Idempotent by run id, so a reload (or a second tab)
  // re-reads rather than appending duplicates.
  land: (slug: string, id: string, runId: string) =>
    postJSON<{ session: Session; message: unknown; drafts: SessionDraft[] }>(
      `${S(slug)}/${encodeURIComponent(id)}/land`, { runId }),

  // --- the plan gate -------------------------------------------------------
  // A planned turn stops once it has a plan and offers it. Starting it runs a second
  // run over the same turn, with whatever the user changed about the arguments.
  approvePlan: (slug: string, id: string, runId: string, plan: AgentPlan) =>
    postJSON<{ session: Session; message: ChatMessage; run: RunMeta }>(
      `${S(slug)}/${encodeURIComponent(id)}/plan/approve`,
      { runId, goal: plan.goal, steps: plan.steps, doneCriteria: plan.done_criteria }),
  cancelPlan: (slug: string, id: string, runId: string) =>
    postJSON<{ session: Session; message: ChatMessage }>(
      `${S(slug)}/${encodeURIComponent(id)}/plan/cancel`, { runId }),

  // --- working copies: the accept / dismiss step ---------------------------
  drafts: (slug: string, id: string) =>
    getJSON<{ drafts: SessionDraft[] }>(`${S(slug)}/${encodeURIComponent(id)}/drafts`)
      .then((d) => d.drafts),
  // Accept: into the global workspace — a new idea.json, or an overwrite of the
  // record the run modified. The working copy is consumed either way.
  // `as` decides where an `update` copy lands: over the record it was refined from
  // (the default), or beside it as a new variant — the two options the thinker's
  // divergence suggestion offers.
  acceptDraft: (slug: string, id: string, draftId: string, as_: "overwrite" | "variant" = "overwrite") =>
    postJSON<{ result: { created: string[]; written: string[] }; session: Session; drafts: SessionDraft[] }>(
      `${S(slug)}/${encodeURIComponent(id)}/drafts/${encodeURIComponent(draftId)}/accept`,
      { as: as_ }),
  // Dismiss: recorded on the working copy as a decision; nothing is written to the
  // knowledge base and the card stays decided across reloads.
  dismissDraft: (slug: string, id: string, draftId: string) =>
    delJSON<{ drafts: SessionDraft[] }>(
      `${S(slug)}/${encodeURIComponent(id)}/drafts/${encodeURIComponent(draftId)}`),

  async upload(slug: string, id: string, file: File): Promise<{ session: Session; added: ContextRef[] }> {
    const fd = new FormData();
    fd.append("file", file);
    const r = await fetch(`${API_BASE}${S(slug)}/${encodeURIComponent(id)}/files`,
      { method: "POST", body: fd });
    if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
    return r.json();
  },
};

// What the orchestrator can call and what each call takes — derived on the backend
// from the tools' own argument models, so the plan editor's fields, ranges and allowed
// values are the ones the tools actually validate against.
export const agentApi = {
  orchestratorTools: () =>
    getJSON<{ tools: ToolSpec[] }>("/api/agents/orchestrator/tools").then((d) => d.tools),
};

export interface SendBody {
  text: string;
  context: { kind: string; id: string }[];
  // Orchestrator only: gap/idea ids (already loaded, or arriving in `context` on this
  // same turn) this turn is actually about — set when more than one thing is in scope
  // and the message would otherwise be ambiguous about which.
  activeIds?: string[];
  // Who runs the turn. "orchestrator" reads the message and decides what to call —
  // it may generate, refine, look something up, or just answer. "creative_thinker"
  // is the direct surface, where the fields below are the call.
  //
  // The fields below are ignored by the orchestrator: choosing them is what its
  // planner is for. They stay because the direct surface still uses them.
  agent?: "orchestrator" | "creative_thinker";
  mode?: "generation" | "refinement";
  ideaId?: string | null;
  width?: number;
  depth?: number;
  styles?: string[];
  toolUse?: boolean;
  maxLlmCalls?: number;
  // Orchestrator only: show the plan and wait for Start (the default) instead of
  // running it straight away.
  requirePlanApproval?: boolean;
}

// A researcher's own edit to an idea (spine fields + the open sections). The
// thinking graph, relations and provenance are not reachable from here — those
// are written by the agents, with a rationale attached.
export function patchIdea(
  slug: string,
  ideaId: string,
  patch: {
    title?: string; oneLiner?: string; description?: string; problem?: string;
    sections?: Record<string, string | string[] | null>; sectionOrder?: string[];
  },
): Promise<Idea> {
  return patchJSON<Idea>(
    `/api/topics/${encodeURIComponent(slug)}/ideas/${encodeURIComponent(ideaId)}`, patch);
}

// A researcher's own edit to a topic's name/description. The category agent
// only writes a starting-point description; this is how the user corrects it.
export function patchTopic(
  slug: string,
  patch: { name?: string; description?: string },
): Promise<TopicMeta> {
  return patchJSON<TopicMeta>(`/api/topics/${encodeURIComponent(slug)}`, patch);
}
