// API client for the ideation backend. Reads are plain fetch; the paper-agent
// run is an SSE stream (the reader mirrors dev/static/index.html::runStreaming).

import type { Idea, Paper, RunEvent, RunMeta, TopicBundle, TopicMeta } from "./types";

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
  // Bootstrap a new category via the category agent (research → describe → write
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
