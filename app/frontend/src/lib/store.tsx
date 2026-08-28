// Workspace store — real data fetched from the backend plus live agent-run
// state. Runs execute *detached* on the backend (app/backend/runs.py): starting
// one returns its meta immediately, events arrive over a replay-and-follow
// stream, and everything survives page reloads — on topic load the store pulls
// the persisted runs list, re-attaches to the running ones, and restores paused
// queue entries (ambiguous / needs_source / cancelled / interrupted).
import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import {
  api, cancelRun as apiCancel, deleteRun as apiDelete, restartRun as apiRestart,
  followRun, listRuns, patchIdea, resumeGap, resumePaperGaps, runIdeaAgent,
  runPaperAgent, sessionApi, uploadPaperSource,
} from "./api";
import type {
  Idea, Nav, RunEvent, RunMeta, RunState, SessionSummary, TopicBundle, TopicMeta,
} from "./types";

interface WorkspaceCtx {
  topics: TopicMeta[];
  activeSlug: string | null;
  active: TopicMeta | null;
  bundle: TopicBundle | null;
  loading: boolean;
  nav: Nav;
  // Every tracked run of this topic, oldest first. Multiple can be "running".
  runs: RunState[];
  // The topic's ideation sessions, newest first (list rows only — a session's
  // transcript is loaded by the session view itself).
  sessions: SessionSummary[];
  setActiveSlug: (slug: string) => void;
  setNav: (nav: Nav) => void;
  refresh: () => Promise<void>;
  // Bootstrap a new category via the category agent, then switch to it. Resolves
  // with the new slug (or null on failure) so the caller can close its form.
  createTopic: (topic: string) => Promise<string | null>;
  // autoOpen: navigate to the paper's page the moment the run attaches to it
  // (single ingests do; batch ingests don't yank the user around).
  // True once the backend has accepted the run. Callers that provide their own
  // feedback can use false to keep the query visible and surface the failure.
  startPaperRun: (query: string, opts?: { force?: boolean; autoOpen?: boolean }) => Promise<boolean>;
  startIdeaRun: (opts: { mode: "generate" | "expand"; sketch?: string; count?: number }) => Promise<void>;
  startGapResume: (gapId: string) => Promise<void>;
  startPaperGapsResume: (paperId: string) => Promise<void>;
  cancelRun: (runId: string) => Promise<void>;
  restartRun: (runId: string) => Promise<void>;
  deleteRun: (runId: string) => Promise<void>;
  // True between clicking Cancel and the run actually stopping. The backend
  // cancels cooperatively (it can't safely abort an in-flight model call), so
  // the UI shows "Cancelling…" immediately rather than looking stuck.
  isCancelling: (runId: string) => boolean;
  // Attach a user-supplied source to a stalled paper, then resume its pipeline.
  uploadSource: (paperId: string, file: File, resumeQuery: string) => Promise<void>;

  // --- sessions ---------------------------------------------------------------
  refreshSessions: () => Promise<void>;
  // Resolves with the new session's id so the caller can navigate into it.
  createSession: (title?: string, context?: { kind: string; id: string }[]) => Promise<string | null>;
  renameSession: (id: string, title: string) => Promise<void>;
  deleteSession: (id: string) => Promise<void>;
  // Follow one run and resolve when its stream closes — the session chat awaits
  // this to land the envelope on the transcript.
  attachRun: (runId: string) => void;
  // A hand edit to an idea (spine + open sections); refreshes the bundle.
  updateIdea: (ideaId: string, patch: Parameters<typeof patchIdea>[2]) => Promise<Idea | null>;
  // One idea with its `runs[]` — the bundle carries the spine only.
  loadIdea: (ideaId: string) => Promise<Idea | null>;
}

const Ctx = createContext<WorkspaceCtx | null>(null);

// The latest run about an entity (paper page, gap page). Newest wins so a
// follow-up run (retry, source upload → resume) replaces the finished one.
export function runForTarget(runs: RunState[], kind: "paper" | "gap", id: string): RunState | undefined {
  for (let i = runs.length - 1; i >= 0; i--) {
    const r = runs[i];
    if (r.target?.kind === kind && r.target.id === id) return r;
  }
  return undefined;
}

function metaToState(meta: RunMeta, events: RunEvent[] = []): RunState {
  return {
    id: meta.run_id,
    agent: meta.agent,
    kind: meta.kind,
    status: meta.status,
    query: meta.query,
    events,
    error: meta.error ?? null,
    resultPaperId: meta.result_paper_id ?? null,
    resultIdeaIds: meta.result_idea_ids ?? [],
    candidates: meta.candidates ?? [],
    target: meta.target ?? null,
    sessionId: meta.session_id ?? null,
    envelope: meta.envelope ?? null,
    note: meta.note ?? null,
  };
}

export function WorkspaceProvider({ children }: { children: ReactNode }) {
  const [topics, setTopics] = useState<TopicMeta[]>([]);
  const [activeSlug, _setActiveSlug] = useState<string | null>(null);
  const [bundle, setBundle] = useState<TopicBundle | null>(null);
  const [loading, setLoading] = useState(true);
  const [nav, setNav] = useState<Nav>({ view: "topic", id: null });
  const [runs, setRuns] = useState<RunState[]>([]);
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [cancelling, setCancelling] = useState<Set<string>>(new Set());
  const slugRef = useRef<string | null>(null);
  const attachedRef = useRef<Set<string>>(new Set());   // run ids with a live event stream
  const autoOpenRef = useRef<Set<string>>(new Set());   // run ids that navigate on paper-attach
  const navigatedRef = useRef<Set<string>>(new Set());  // run ids that already navigated

  // Initial topic list.
  useEffect(() => {
    api.topics()
      .then((ts) => {
        setTopics(ts);
        if (ts.length && !slugRef.current) {
          slugRef.current = ts[0].slug;
          _setActiveSlug(ts[0].slug);
        }
      })
      .catch(() => setTopics([]));
  }, []);

  const loadBundle = useCallback(async (slug: string) => {
    setLoading(true);
    try {
      setBundle(await api.topic(slug));
    } catch {
      setBundle(null);
    } finally {
      setLoading(false);
    }
  }, []);

  // Silent reload (no loading flag) — for live updates mid-run without flicker.
  const reloadBundle = useCallback(async () => {
    const slug = slugRef.current;
    if (!slug) return;
    try { setBundle(await api.topic(slug)); } catch { /* keep last good bundle */ }
  }, []);

  const refresh = useCallback(async () => {
    await Promise.all([
      api.topics().then(setTopics).catch(() => {}),
      slugRef.current ? loadBundle(slugRef.current) : Promise.resolve(),
    ]);
  }, [loadBundle]);

  // --- run state plumbing -------------------------------------------------------

  const mergeMeta = useCallback((slug: string, meta: RunMeta) => {
    if (slug !== slugRef.current) return; // stale merge after a topic switch
    setRuns((rs) => {
      const i = rs.findIndex((r) => r.id === meta.run_id);
      if (i === -1) return [...rs, metaToState(meta)];
      const next = [...rs];
      next[i] = metaToState(meta, next[i].events);
      return next;
    });
  }, []);

  const pushEvent = useCallback((slug: string, runId: string, ev: RunEvent) => {
    if (slug !== slugRef.current) return;
    setRuns((rs) => rs.map((r) => (r.id === runId ? { ...r, events: [...r.events, ev] } : r)));
  }, []);

  // Follow one run's event stream (replay from 0, then live). The paper-attach
  // watch fires the instant the fetch stage reports a paper id: tag the run,
  // pull the fresh bundle (the sidebar shows the new paper), and — for autoOpen
  // runs — open its page, where the full transcript streams.
  const attach = useCallback((slug: string, runId: string) => {
    if (attachedRef.current.has(runId)) return;
    attachedRef.current.add(runId);
    followRun(runId, 0, {
      onEvent: (ev) => {
        pushEvent(slug, runId, ev);
        if (ev.kind === "node" && !navigatedRef.current.has(runId)) {
          const pid = (ev.result as Record<string, unknown> | null | undefined)?.["paper_id"];
          if (typeof pid === "string" && pid) {
            navigatedRef.current.add(runId);
            setRuns((rs) => rs.map((r) =>
              r.id === runId && !r.target ? { ...r, target: { kind: "paper", id: pid }, resultPaperId: pid } : r));
            reloadBundle();
            if (autoOpenRef.current.has(runId) && slug === slugRef.current)
              setNav({ view: "paper", id: pid });
          }
        }
      },
      onMeta: (meta) => {
        if (meta) mergeMeta(slug, meta);
        reloadBundle();
      },
    })
      .catch(() => { /* the poll below re-attaches if the run is still live */ })
      .finally(() => { attachedRef.current.delete(runId); });
  }, [mergeMeta, pushEvent, reloadBundle]);

  // Pull the persisted runs list and re-attach to everything still running —
  // this is what restores the queue (and live transcripts) after a page reload.
  const syncRuns = useCallback(async (slug: string) => {
    let metas: RunMeta[];
    try { metas = await listRuns(slug); } catch { return; }
    if (slug !== slugRef.current) return;
    setRuns((rs) => {
      const byId = new Map(rs.map((r) => [r.id, r]));
      return metas.map((m) => {
        const prev = byId.get(m.run_id);
        return metaToState(m, prev?.events ?? []);
      });
    });
    for (const m of metas) if (m.status === "running") attach(slug, m.run_id);
  }, [attach]);

  const refreshSessions = useCallback(async () => {
    const slug = slugRef.current;
    if (!slug) return;
    try { setSessions(await sessionApi.list(slug)); } catch { setSessions([]); }
  }, []);

  // Load bundle + runs + sessions whenever the active topic changes.
  useEffect(() => {
    if (!activeSlug) return;
    loadBundle(activeSlug);
    syncRuns(activeSlug);
    refreshSessions();
  }, [activeSlug, loadBundle, syncRuns, refreshSessions]);

  // While any run is live, poll the KB (intermediate results appear as they
  // land) and the runs list (statuses flip, streams re-attach after hiccups).
  const anyRunning = runs.some((r) => r.status === "running");
  useEffect(() => {
    if (!anyRunning) return;
    const id = setInterval(() => {
      reloadBundle();
      refreshSessions();
      if (slugRef.current) syncRuns(slugRef.current);
    }, 2500);
    return () => clearInterval(id);
  }, [anyRunning, reloadBundle, refreshSessions, syncRuns]);

  const setActiveSlug = useCallback((slug: string) => {
    slugRef.current = slug;
    _setActiveSlug(slug);
    setNav({ view: "topic", id: null });
    setRuns([]);
    setSessions([]);
  }, []);

  // Create a category (the category agent writes its meta.json), add it to the
  // topic list, and make it the active workspace.
  const createTopic = useCallback(async (topic: string): Promise<string | null> => {
    if (!topic.trim()) return null;
    try {
      const meta = await api.createTopic(topic.trim());
      setTopics((ts) => {
        const i = ts.findIndex((t) => t.slug === meta.slug);
        if (i === -1) return [...ts, meta];
        const next = [...ts];
        next[i] = meta;   // re-creating an existing topic refreshes it in place
        return next;
      });
      setActiveSlug(meta.slug);
      return meta.slug;
    } catch (e) {
      console.error("create topic failed:", e);
      return null;
    }
  }, [setActiveSlug]);

  // --- actions ----------------------------------------------------------------

  const startPaperRun = useCallback(
    async (query: string, opts: { force?: boolean; autoOpen?: boolean } = {}) => {
      const slug = slugRef.current;
      if (!slug || !query.trim()) return false;
      try {
        const meta = await runPaperAgent(slug, query.trim(), opts.force ?? false);
        if (opts.autoOpen ?? true) autoOpenRef.current.add(meta.run_id);
        mergeMeta(slug, meta);
        if (meta.status === "running") attach(slug, meta.run_id);
        return true;
      } catch (e) {
        console.error("start paper run failed:", e);
        return false;
      }
    },
    [attach, mergeMeta],
  );

  const startIdeaRun = useCallback(
    async ({ mode, sketch = "", count = 3 }: { mode: "generate" | "expand"; sketch?: string; count?: number }) => {
      const slug = slugRef.current;
      if (!slug || (mode === "expand" && !sketch.trim())) return;
      try {
        const meta = await runIdeaAgent(slug, { mode, sketch: sketch.trim(), count });
        mergeMeta(slug, meta);
        if (meta.status === "running") attach(slug, meta.run_id);
      } catch (e) {
        console.error("start idea run failed:", e);
      }
    },
    [attach, mergeMeta],
  );

  const startGapResume = useCallback(async (gapId: string) => {
    const slug = slugRef.current;
    if (!slug) return;
    const meta = await resumeGap(slug, gapId);
    mergeMeta(slug, meta);
    if (meta.status === "running") attach(slug, meta.run_id);
  }, [attach, mergeMeta]);

  const startPaperGapsResume = useCallback(async (paperId: string) => {
    const slug = slugRef.current;
    if (!slug) return;
    const meta = await resumePaperGaps(slug, paperId);
    mergeMeta(slug, meta);
    if (meta.status === "running") attach(slug, meta.run_id);
  }, [attach, mergeMeta]);

  const cancelRun = useCallback(async (runId: string) => {
    const slug = slugRef.current;
    setCancelling((s) => new Set(s).add(runId)); // instant "Cancelling…" feedback
    try { await apiCancel(runId); } catch { /* already terminal */ }
    if (slug) await syncRuns(slug);
  }, [syncRuns]);

  const isCancelling = useCallback((runId: string) => cancelling.has(runId), [cancelling]);

  // Drop the "cancelling" flag once a run has actually left the running state.
  useEffect(() => {
    if (!cancelling.size) return;
    const stillRunning = new Set(runs.filter((r) => r.status === "running").map((r) => r.id));
    setCancelling((s) => {
      const next = new Set([...s].filter((id) => stillRunning.has(id)));
      return next.size === s.size ? s : next;
    });
  }, [runs, cancelling.size]);

  const restartRun = useCallback(async (runId: string) => {
    const slug = slugRef.current;
    if (!slug) return;
    try {
      const fresh = await apiRestart(runId);
      // The old entry is gone server-side; resync replaces it in one pass.
      if (autoOpenRef.current.delete(runId)) autoOpenRef.current.add(fresh.run_id);
      await syncRuns(slug);
      if (fresh.status === "running") attach(slug, fresh.run_id);
    } catch (e) {
      console.error("restart failed:", e);
    }
  }, [attach, syncRuns]);

  const deleteRun = useCallback(async (runId: string) => {
    const slug = slugRef.current;
    try { await apiDelete(runId); } catch (e) { console.error("delete failed:", e); }
    if (slug) await syncRuns(slug);
  }, [syncRuns]);

  const uploadSource = useCallback(
    async (paperId: string, file: File, resumeQuery: string) => {
      const slug = slugRef.current;
      if (!slug) return;
      await uploadPaperSource(slug, paperId, file);
      // The source is in place; resume the (idempotent) pipeline from the query
      // the paper was fetched with — extract now finds the uploaded source.
      await startPaperRun(resumeQuery);
    },
    [startPaperRun],
  );

  // --- sessions ---------------------------------------------------------------

  const createSession = useCallback(
    async (title = "", context: { kind: string; id: string }[] = []): Promise<string | null> => {
      const slug = slugRef.current;
      if (!slug) return null;
      try {
        const rec = await sessionApi.create(slug, title, context);
        await refreshSessions();
        return rec.session_id;
      } catch (e) {
        console.error("create session failed:", e);
        return null;
      }
    },
    [refreshSessions],
  );

  const renameSession = useCallback(async (id: string, title: string) => {
    const slug = slugRef.current;
    if (!slug) return;
    try { await sessionApi.rename(slug, id, title); } catch (e) { console.error(e); }
    await refreshSessions();
  }, [refreshSessions]);

  const deleteSession = useCallback(async (id: string) => {
    const slug = slugRef.current;
    if (!slug) return;
    try { await sessionApi.remove(slug, id); } catch (e) { console.error(e); }
    await refreshSessions();
    setNav((n) => (n.view === "session" && n.id === id ? { view: "sessions", id: null } : n));
  }, [refreshSessions]);

  // Public wrapper over the internal `attach` — the session chat starts its run
  // through the REST endpoint (which records the turn), then asks the store to
  // follow it so the run joins the same queue every other run lives in.
  const attachRun = useCallback((runId: string) => {
    const slug = slugRef.current;
    if (!slug) return;
    syncRuns(slug);
    attach(slug, runId);
  }, [attach, syncRuns]);

  // --- ideas ------------------------------------------------------------------

  const updateIdea = useCallback(
    async (ideaId: string, patch: Parameters<typeof patchIdea>[2]) => {
      const slug = slugRef.current;
      if (!slug) return null;
      try {
        const idea = await patchIdea(slug, ideaId, patch);
        await reloadBundle();
        return idea;
      } catch (e) {
        console.error("idea patch failed:", e);
        return null;
      }
    },
    [reloadBundle],
  );

  const loadIdea = useCallback(async (ideaId: string) => {
    const slug = slugRef.current;
    if (!slug) return null;
    try { return await api.idea(slug, ideaId); } catch { return null; }
  }, []);

  const active = topics.find((t) => t.slug === activeSlug) || bundle?.meta || null;

  return (
    <Ctx.Provider
      value={{
        topics, activeSlug, active, bundle, loading, nav, runs, sessions,
        setActiveSlug, setNav, refresh, createTopic, startPaperRun, startIdeaRun,
        uploadSource, startGapResume, startPaperGapsResume, cancelRun, restartRun,
        deleteRun, isCancelling, refreshSessions, createSession, renameSession,
        deleteSession, attachRun, updateIdea, loadIdea,
      }}
    >
      {children}
    </Ctx.Provider>
  );
}

export function useWorkspace(): WorkspaceCtx {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useWorkspace must be used within WorkspaceProvider");
  return ctx;
}
