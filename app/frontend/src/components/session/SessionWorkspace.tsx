// One ideation session — the chat on the left, a column of workspace windows on
// the right, a draggable splitter between them.
//
// The same component renders a **new** session (`sessionId = null`): the page is
// identical, and the record is created on the first turn rather than by a
// separate "create" step. That is why the sidebar's *Sessions* header opens this
// straight away — an empty session and a new one look the same because they are.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Icon } from "../Icon";
import { Chat } from "./Chat";
import type { ComposerSettings } from "./Chat";
import { PanelColumn } from "./Panels";
import type { DraftActions } from "./EnvelopeResult";
import type { Mark } from "../IdeaGraph";
import { sessionApi } from "../../lib/api";
import { graphGaps } from "../../lib/graphdata";
import { useWorkspace } from "../../lib/store";
import type {
  ContextRef, Panel, Session, SessionDraft,
} from "../../lib/types";

const DEFAULT_PANELS: Panel[] = [
  { uid: "p-graph", kind: "graph", ref: null, weight: 1.35 },
  { uid: "p-thoughts", kind: "thoughts", ref: null, weight: 1 },
];

export function SessionWorkspace({ sessionId }: { sessionId: string | null }) {
  const {
    activeSlug, bundle, runs, sessions, setNav, refreshSessions, attachRun, refresh,
  } = useWorkspace();

  const [session, setSession] = useState<Session | null>(null);
  const [staged, setStaged] = useState<ContextRef[]>([]);
  const [panels, setPanels] = useState<Panel[]>(DEFAULT_PANELS);
  const [chatWidth, setChatWidth] = useState(0.46);   // fraction of the shell
  const [sending, setSending] = useState(false);
  // The session's unaccepted working copies — real records in its own folder,
  // one per draft the runs produced plus one per idea a run modified.
  const [drafts, setDrafts] = useState<SessionDraft[]>([]);
  const [deciding, setDeciding] = useState<string | null>(null);
  const [settings, setSettings] = useState<ComposerSettings>({
    mode: "generation", ideaId: null, width: 3, depth: 3, styles: [], toolUse: false,
  });

  const shellRef = useRef<HTMLDivElement>(null);
  const ideas = bundle?.ideas ?? [];
  const gaps = useMemo(() => graphGaps(bundle), [bundle]);
  const summary = sessions.find((s) => s.session_id === sessionId);

  // --- loading -----------------------------------------------------------------

  const reload = useCallback(async () => {
    if (!activeSlug || !sessionId) { setSession(null); setDrafts([]); return; }
    try { setSession(await sessionApi.get(activeSlug, sessionId)); } catch { setSession(null); }
    try { setDrafts(await sessionApi.drafts(activeSlug, sessionId)); } catch { setDrafts([]); }
  }, [activeSlug, sessionId]);

  useEffect(() => {
    setStaged([]);
    reload();
  }, [reload]);

  // Runs belonging to this session — the transcript and the run panel both read
  // them, and a reload re-attaches through the store's own queue.
  const sessionRuns = useMemo(
    () => runs.filter((r) => (sessionId ? r.sessionId === sessionId : false)),
    [runs, sessionId],
  );

  // When a run finishes, write its envelope onto the transcript. Idempotent by
  // run id server-side, so a reload or a second tab re-reads rather than appends.
  const landedRef = useRef<Set<string>>(new Set());
  useEffect(() => {
    if (!activeSlug || !sessionId) return;
    for (const run of sessionRuns) {
      if (run.status === "running" || landedRef.current.has(run.id)) continue;
      landedRef.current.add(run.id);
      sessionApi.land(activeSlug, sessionId, run.id)
        .then((r) => { setSession(r.session); setDrafts(r.drafts); })
        .catch(() => landedRef.current.delete(run.id));
    }
  }, [activeSlug, sessionId, sessionRuns]);

  // --- context -----------------------------------------------------------------

  const stage = useCallback((refs: ContextRef[]) => {
    setStaged((cur) => {
      const have = new Set(cur.map((c) => `${c.kind}:${c.id}`));
      const loaded = new Set((session?.context ?? []).map((c) => `${c.kind}:${c.id}`));
      return [...cur, ...refs.filter((r) => {
        const k = `${r.kind}:${r.id}`;
        if (have.has(k) || loaded.has(k)) return false;
        have.add(k);
        return true;
      })];
    });
  }, [session]);

  const unstage = (ref: ContextRef) =>
    setStaged((cur) => cur.filter((c) => !(c.kind === ref.kind && c.id === ref.id)));

  const toggleMark = (kind: "gap" | "idea", id: string) => {
    const k = `${kind}:${id}`;
    if ((session?.context ?? []).some((c) => `${c.kind}:${c.id}` === k)) return; // already loaded
    if (staged.some((c) => `${c.kind}:${c.id}` === k)) {
      unstage({ kind, id, label: "", digest: "" });
      return;
    }
    const label = kind === "gap"
      ? gaps.find((g) => g.id === id)?.title ?? id
      : ideas.find((i) => i.id === id)?.title ?? id;
    stage([{ kind, id, label, digest: "" }]);
  };

  const unload = async (ref: ContextRef) => {
    if (!activeSlug || !sessionId) return;
    await sessionApi.removeContext(activeSlug, sessionId, ref.kind, ref.id);
    reload();
  };

  const uploadFile = async (file: File) => {
    if (!activeSlug) return;
    let id = sessionId;
    if (!id) {
      const rec = await sessionApi.create(activeSlug, "");
      id = rec.session_id;
      await refreshSessions();
      setNav({ view: "session", id });
    }
    const r = await sessionApi.upload(activeSlug, id!, file);
    setSession(r.session);
  };

  const marks = useMemo(() => {
    const m = new Map<string, Mark>();
    for (const c of session?.context ?? []) m.set(`${c.kind}:${c.id}`, "loaded");
    for (const c of staged) m.set(`${c.kind}:${c.id}`, "staged");
    return m;
  }, [session, staged]);

  // --- sending -----------------------------------------------------------------

  const send = async (text: string) => {
    if (!activeSlug || sending) return;
    setSending(true);
    try {
      let id = sessionId;
      if (!id) {
        const rec = await sessionApi.create(activeSlug, text.slice(0, 60));
        id = rec.session_id;
        setSession(rec);
        setNav({ view: "session", id });
      }
      const res = await sessionApi.send(activeSlug, id!, {
        text,
        context: staged.map((c) => ({ kind: c.kind, id: c.id })),
        mode: settings.mode,
        ideaId: settings.mode === "refinement" ? settings.ideaId : null,
        width: settings.width,
        depth: settings.depth,
        styles: settings.styles,
        toolUse: settings.toolUse,
      });
      setSession(res.session);
      setStaged([]);
      attachRun(res.run.run_id);
      refreshSessions();
    } catch (e) {
      console.error("send failed:", e);
      alert(`Could not start the run: ${e}`);
    } finally {
      setSending(false);
    }
  };

  // --- accept / dismiss --------------------------------------------------------
  // The orchestrator's write step, done by hand. Accepting refreshes the bundle
  // too: a new idea has to appear in the graph and the nav tree at once, or the
  // decision looks like it did not take.
  //
  // Neither accept nor dismiss removes the working copy: the decision is stored on
  // it, and the transcript keeps rendering it as decided after a reload.

  const draftActions: DraftActions = useMemo(() => ({
    find: (runId, kind, index) => drafts.find(
      (d) => d._session.run_id === runId && d._session.kind === kind
        && d._session.source_index === index),
    accept: async (draftId, as_ = "overwrite") => {
      if (!activeSlug || !sessionId) return;
      setDeciding(draftId);
      try {
        const r = await sessionApi.acceptDraft(activeSlug, sessionId, draftId, as_);
        setDrafts(r.drafts);
        setSession(r.session);
        await refresh();
      } catch (e) {
        console.error("accept failed:", e);
        alert(`Could not save it: ${e}`);
      } finally {
        setDeciding(null);
      }
    },
    dismiss: async (draftId) => {
      if (!activeSlug || !sessionId) return;
      setDeciding(draftId);
      try {
        setDrafts((await sessionApi.dismissDraft(activeSlug, sessionId, draftId)).drafts);
      } catch (e) {
        console.error("dismiss failed:", e);
      } finally {
        setDeciding(null);
      }
    },
    busy: deciding,
  }), [activeSlug, sessionId, drafts, deciding, refresh]);

  // Adds a window, unless one of that kind is already open — a second identical
  // window is never what "show me this" meant.
  const openPanel = (kind: Panel["kind"], ref: string | null) =>
    setPanels((ps) => (ps.some((p) => p.kind === kind && p.ref === ref)
      ? ps
      : [...ps, { uid: `p${Date.now()}`, kind, ref, weight: 1 }]));

  // --- the splitter -------------------------------------------------------------

  const onSplitterDown = (e: React.MouseEvent) => {
    e.preventDefault();
    const move = (ev: MouseEvent) => {
      const box = shellRef.current?.getBoundingClientRect();
      if (!box) return;
      setChatWidth(Math.min(0.72, Math.max(0.28, (ev.clientX - box.left) / box.width)));
    };
    const up = () => {
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", up);
    };
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
  };

  return (
    <div className="session">
      <div className="session-bar">
        <Icon name="msg" />
        <span className="session-title">
          {session?.title || summary?.title || "New session"}
        </span>
        {session ? (
          <span className="session-stats">
            {session.messages.length} turn{session.messages.length === 1 ? "" : "s"} ·{" "}
            {session.context.length} in context
            {session.touched.length > 0 && ` · ${session.touched.length} touched`}
          </span>
        ) : (
          <span className="session-new-pip">unsaved — the first turn creates it</span>
        )}
        <div className="session-bar-right">
          <button className="btn btn--ghost" onClick={() => setNav({ view: "idea-graph", id: null })}>
            <Icon name="share" /> Full graph
          </button>
        </div>
      </div>

      <div className="session-shell" ref={shellRef}>
        <div className="session-chat" style={{ flexBasis: `${chatWidth * 100}%` }}>
          <Chat
            session={session}
            staged={staged}
            runs={sessionRuns}
            ideas={ideas}
            settings={settings}
            sending={sending}
            onStage={stage}
            onUnstage={unstage}
            onUploadFile={uploadFile}
            onSend={send}
            onSettings={(p) => setSettings((s) => ({ ...s, ...p }))}
            onOpenIdea={(id) => openPanel("idea", id)}
            drafts={draftActions}
            onShowThoughts={() => openPanel("thoughts", null)}
          />
        </div>

        <div className="session-splitter" onMouseDown={onSplitterDown} title="Drag to resize">
          <span />
        </div>

        <div className="session-panels">
          <PanelColumn
            panels={panels}
            onChange={setPanels}
            gaps={gaps}
            ideas={ideas}
            runs={sessionRuns}
            marks={marks}
            loaded={session?.context ?? []}
            onToggleMark={toggleMark}
            onOpenNode={(kind, id) => setNav({ view: kind === "gap" ? "gap" : "idea", id })}
            onUnload={unload}
            onOpenIdea={(id) => setNav({ view: "idea", id })}
          />
        </div>
      </div>
    </div>
  );
}
