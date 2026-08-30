// One ideation session — the chat on the left, a column of workspace windows on
// the right, a draggable splitter between them.
//
// The same component renders a **new** session (`sessionId = null`): the page is
// identical, and the record is created on the first turn rather than by a
// separate "create" step. That is why the sidebar's *Sessions* header opens this
// straight away — an empty session and a new one look the same because they are.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Icon } from "../Icon";
import { NavHistory } from "../NavHistory";
import { TopSearch } from "../TopSearch";
import { Chat } from "./Chat";
import type { PlanActions } from "./Chat";
import { KIND_LABEL, PanelGrid } from "./Panels";
import type { PlannedTurn } from "./Panels";
import type { DraftActions } from "./EnvelopeResult";
import type { Mark } from "../IdeaGraph";
import { sessionApi } from "../../lib/api";
import { graphGaps } from "../../lib/graphdata";
import { useRenameField } from "../../lib/renameField";
import { useWorkspace } from "../../lib/store";
import type {
  AgentPlan, ContextRef, PanelKind, Row, Session, SessionDraft,
} from "../../lib/types";

// Chat on the left, the two workspace windows stacked on the right — the layout
// this session had before any of it could be rearranged. Every window here,
// chat included, can be dragged anywhere in the grid.
const DEFAULT_ROWS: Row[] = [
  {
    uid: "r-main",
    weight: 1,
    columns: [
      { uid: "c-chat", weight: 0.85, panels: [{ uid: "p-chat", kind: "chat", ref: null, weight: 1 }] },
      {
        uid: "c-work",
        weight: 1.15,
        panels: [
          // The plan first: it is what the run is *about to do*, and the window the Start
          // button in the chat refers to.
          { uid: "p-plan", kind: "plan", ref: null, weight: 1 },
          { uid: "p-graph", kind: "graph", ref: null, weight: 1.2 },
          { uid: "p-thoughts", kind: "thoughts", ref: null, weight: 1 },
        ],
      },
    ],
  },
];

/** Every window in the grid, left to right, top to bottom. */
const allPanels = (rows: Row[]) => rows.flatMap((r) => r.columns.flatMap((c) => c.panels));

export function SessionWorkspace({ sessionId }: { sessionId: string | null }) {
  const {
    activeSlug, bundle, runs, sessions, setNav, refreshSessions, attachRun, refresh,
    renameSession, deleteSession,
  } = useWorkspace();

  const [session, setSession] = useState<Session | null>(null);
  // What this turn is about: a paper/note/file staged here is new context about to
  // load; a gap/idea staged here is *also* the turn's subject — selecting and staging
  // are the same action, so an id already loaded can be staged again just to say
  // "this one" without re-adding it (loading is idempotent server-side).
  const [staged, setStaged] = useState<ContextRef[]>([]);
  const [rows, setRows] = useState<Row[]>(DEFAULT_ROWS);
  const [sending, setSending] = useState(false);
  // The session's unaccepted working copies — real records in its own folder,
  // one per draft the runs produced plus one per idea a run modified.
  const [drafts, setDrafts] = useState<SessionDraft[]>([]);
  const [deciding, setDeciding] = useState<string | null>(null);
  // The proposed plan currently being started, by the run id that proposed it.
  const [starting, setStarting] = useState<string | null>(null);
  // The session's name while it is being edited in the bar, or null when it isn't.
  const [renaming, setRenaming] = useState<string | null>(null);

  const ideas = bundle?.ideas ?? [];
  const gaps = useMemo(() => graphGaps(bundle), [bundle]);
  const summary = sessions.find((s) => s.session_id === sessionId);
  // The summary wins over the loaded record for the *name*: a new session is named
  // from its first task in the background, and the session list is what re-reads
  // while the run is live — the record in hand still carries the placeholder.
  const title = summary?.title || session?.title || "New session";

  const commitRename = useCallback((next: string) => {
    const name = next.trim();
    setRenaming(null);
    if (!sessionId || !name || name === title) return;
    renameSession(sessionId, name);
    setSession((cur) => (cur ? { ...cur, title: name } : cur));
  }, [renameSession, sessionId, title]);

  const removeSession = useCallback(() => {
    if (!sessionId) return;
    if (confirm(`Delete "${title}"? Its transcript, loaded context and unaccepted drafts go with it.`))
      deleteSession(sessionId);  // the store navigates back to a new session
  }, [deleteSession, sessionId, title]);

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

  // Staging is idempotent by id, but no longer skips ids already in `session.context` —
  // restaging an already-loaded gap/idea is exactly how you select it as this turn's
  // subject without re-adding it (the backend's own add is idempotent too).
  const stage = useCallback((refs: ContextRef[]) => {
    setStaged((cur) => {
      const have = new Set(cur.map((c) => `${c.kind}:${c.id}`));
      return [...cur, ...refs.filter((r) => {
        const k = `${r.kind}:${r.id}`;
        if (have.has(k)) return false;
        have.add(k);
        return true;
      })];
    });
  }, []);

  const unstage = (ref: ContextRef) =>
    setStaged((cur) => cur.filter((c) => !(c.kind === ref.kind && c.id === ref.id)));

  // Clicking a node: stage it if it isn't already selected, unstage it if it is —
  // one toggle, whether the node is new, already staged, or already loaded. A loaded
  // node just cycles through "selected" and back to plain background context.
  const toggleMark = (kind: "gap" | "idea", id: string) => {
    const k = `${kind}:${id}`;
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
    unstage(ref);
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

  // Every planned turn of this session, in order — what the plan window reads when no
  // run of it is streaming any more.
  const planned = useMemo<PlannedTurn[]>(
    () => (session?.messages ?? [])
      .filter((m) => m.plan)
      .map((m) => ({ plan: m.plan!, status: m.plan_status ?? "proposed" })),
    [session]);

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
        // Every staged gap/idea is this turn's subject — whether it's new context or
        // one already loaded that got reselected.
        activeIds: staged.filter((c) => c.kind === "gap" || c.kind === "idea")
          .map((c) => c.id),
        agent: "orchestrator",
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

  // --- the plan gate -----------------------------------------------------------
  // A planned turn is shown before it runs. Starting it is a second run over the same
  // turn — the same shape as accepting a draft: what the agent produced is an offer,
  // and the user's click is what makes it happen.

  const planActions: PlanActions = useMemo(() => ({
    start: async (runId: string, plan: AgentPlan) => {
      if (!activeSlug || !sessionId || starting) return;
      setStarting(runId);
      try {
        const r = await sessionApi.approvePlan(activeSlug, sessionId, runId, plan);
        setSession(r.session);
        attachRun(r.run.run_id);
      } catch (e) {
        console.error("starting the plan failed:", e);
        alert(`Could not start the plan: ${e}`);
      } finally {
        setStarting(null);
      }
    },
    cancel: async (runId: string) => {
      if (!activeSlug || !sessionId) return;
      try {
        setSession((await sessionApi.cancelPlan(activeSlug, sessionId, runId)).session);
      } catch (e) {
        console.error("discarding the plan failed:", e);
      }
    },
    busy: starting,
  }), [activeSlug, sessionId, starting, attachRun]);

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
    forRun: (runId) => drafts.filter((d) => d._session.run_id === runId),
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

  /** Put a new window at the bottom of the last column of the last row — the
   *  far side from the chat in the default layout, and never on top of what
   *  you were reading. */
  const addPanel = (kind: PanelKind, ref: string | null) =>
    setRows((rs) => {
      if (!rs.length) {
        return [{ uid: `r${Date.now()}`, weight: 1,
          columns: [{ uid: `c${Date.now()}`, weight: 1,
            panels: [{ uid: `p${Date.now()}`, kind, ref, weight: 1 }] }] }];
      }
      const lastRow = rs.length - 1;
      return rs.map((r, ri) => {
        if (ri !== lastRow) return r;
        if (!r.columns.length) {
          return { ...r, columns: [{ uid: `c${Date.now()}`, weight: 1,
            panels: [{ uid: `p${Date.now()}`, kind, ref, weight: 1 }] }] };
        }
        const lastCol = r.columns.length - 1;
        return {
          ...r,
          columns: r.columns.map((c, ci) => (ci !== lastCol ? c : {
            ...c, panels: [...c.panels, { uid: `p${Date.now()}`, kind, ref, weight: 1 }],
          })),
        };
      });
    });

  // Adds a window, unless one of that kind is already open — a second identical
  // window is never what "show me this" meant.
  const openPanel = (kind: PanelKind, ref: string | null) => {
    if (allPanels(rows).some((p) => p.kind === kind && p.ref === ref)) return;
    addPanel(kind, ref);
  };

  // Which windows are open is one decision, so it is one control: a menu in the
  // session bar. Ticking a kind opens it, unticking closes every window of that
  // kind — the windows themselves no longer choose what they are.
  const toggleKind = (kind: PanelKind) => {
    if (!allPanels(rows).some((p) => p.kind === kind)) { addPanel(kind, null); return; }
    setRows((rs) => rs
      .map((r) => ({
        ...r,
        columns: r.columns
          .map((c) => ({ ...c, panels: c.panels.filter((p) => p.kind !== kind) }))
          .filter((c) => c.panels.length > 0),
      }))
      .filter((r) => r.columns.length > 0));
  };

  return (
    <div className="session">
      <div className="session-bar">
        <NavHistory />
        <Icon name="msg" />
        {renaming === null ? (
          <span
            className={"session-title" + (sessionId ? " session-title--editable" : "")}
            onDoubleClick={() => sessionId && setRenaming(title)}
            title={sessionId ? "Double-click to rename" : undefined}
          >
            {title}
          </span>
        ) : (
          <SessionRename
            value={renaming}
            onChange={setRenaming}
            onCommit={() => commitRename(renaming)}
            onCancel={() => setRenaming(null)}
          />
        )}
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
          <TopSearch />
          <WindowsMenu open={allPanels(rows).map((p) => p.kind)} onToggle={toggleKind} />
          <button className="btn btn--ghost" onClick={() => setNav({ view: "idea-graph", id: null })}>
            <Icon name="share" /> Full graph
          </button>
          {sessionId && (
            <SessionMenu
              onRename={() => setRenaming(title)}
              onDelete={removeSession}
            />
          )}
        </div>
      </div>

      <PanelGrid
        rows={rows}
        onChange={setRows}
        gaps={gaps}
        ideas={ideas}
        runs={sessionRuns}
        planned={planned}
        marks={marks}
        loaded={session?.context ?? []}
        onToggleMark={toggleMark}
        onOpenNode={(kind, id) => setNav({ view: kind === "gap" ? "gap" : "idea", id })}
        onUnload={unload}
        onOpenIdea={(id) => setNav({ view: "idea", id })}
        renderChat={(grip) => (
          <Chat
            grip={grip}
            session={session}
            staged={staged}
            runs={sessionRuns}
            ideas={ideas}
            sending={sending}
            onStage={stage}
            onUnstage={unstage}
            onUploadFile={uploadFile}
            onSend={send}
            onOpenIdea={(id) => openPanel("idea", id)}
            drafts={draftActions}
            plans={planActions}
            onShowThoughts={() => openPanel("thoughts", null)}
          />
        )}
      />
    </div>
  );
}

/** The session's name, being edited in place in the bar. */
function SessionRename({
  value, onChange, onCommit, onCancel,
}: {
  value: string; onChange: (v: string) => void; onCommit: () => void; onCancel: () => void;
}) {
  const { ref, blurCancels } = useRenameField();
  return (
    <input
      ref={ref}
      className="session-title-input"
      value={value}
      onChange={(e) => onChange(e.target.value)}
      onBlur={() => { if (blurCancels) onCancel(); }}
      onKeyDown={(e) => {
        if (e.key === "Enter") onCommit();
        else if (e.key === "Escape") onCancel();
      }}
    />
  );
}

/** Rename and delete for the open session — the two things you can do *to* a
 *  session rather than in it, behind one button so neither is a stray click away
 *  from the transcript. */
function SessionMenu({ onRename, onDelete }: { onRename: () => void; onDelete: () => void }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div className="windows-menu" ref={ref}>
      <button
        className={"btn btn--ghost" + (open ? " btn--ghost-open" : "")}
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        title="Session"
      >
        <Icon name="more" />
      </button>
      {open && (
        <div className="windows-menu-list windows-menu-list--narrow">
          <button className="windows-menu-row"
            onClick={() => { setOpen(false); onRename(); }}>
            <Icon name="edit" /> Rename session
          </button>
          <button className="windows-menu-row windows-menu-row--danger"
            onClick={() => { setOpen(false); onDelete(); }}>
            <Icon name="x" /> Delete session
          </button>
        </div>
      )}
    </div>
  );
}

/** The one place that decides which windows are on screen. */
function WindowsMenu({
  open, onToggle,
}: { open: PanelKind[]; onToggle: (k: PanelKind) => void }) {
  const [menu, setMenu] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!menu) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setMenu(false);
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setMenu(false); };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [menu]);

  const kinds = Object.keys(KIND_LABEL) as PanelKind[];
  return (
    <div className="windows-menu" ref={ref}>
      <button
        className={"btn btn--ghost" + (menu ? " btn--ghost-open" : "")}
        onClick={() => setMenu((v) => !v)}
        aria-expanded={menu}
      >
        <Icon name="grid" /> Windows
        <span className="windows-menu-count">{open.length}</span>
        <Icon name="chevron-down" />
      </button>
      {menu && (
        <div className="windows-menu-list">
          {kinds.map((k) => {
            const on = open.includes(k);
            return (
              <button
                key={k}
                className={"windows-menu-row" + (on ? " windows-menu-row--on" : "")}
                onClick={() => onToggle(k)}
              >
                <span className="windows-menu-check">{on && <Icon name="check" />}</span>
                {KIND_LABEL[k]}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
