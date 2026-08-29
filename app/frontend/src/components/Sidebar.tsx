// Sidebar v2 — workspace brand, topic card with a real topic picker, and
// per-topic Papers / Gaps / Ideas sections fed from the backend. Entities with
// a live agent run get a spinner, so parallel runs are visible at a glance.
import { useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { Icon, SeverityDot } from "./Icon";
import { useWorkspace } from "../lib/store";
import { refDragProps } from "../lib/dnd";
import type { DragRef } from "../lib/dnd";
import type { Nav, RunState } from "../lib/types";
import { DEFAULT_RANKING_CONFIG, fmtScore, overallScore } from "../lib/ranking";

// Which papers/gaps are being worked on right now. Papers come from each running
// run's target; gaps from the per-gap trace events the parallel validate/rank
// phases emit (a gap is busy between its `_start` event and its done/failed one).
function busyEntities(runs: RunState[]) {
  const papers = new Set<string>();
  const gaps = new Set<string>();
  for (const r of runs) {
    if (r.status !== "running") continue;
    if (r.target?.kind === "paper") papers.add(r.target.id);
    if (r.target?.kind === "gap") gaps.add(r.target.id);
    const inFlight = new Set<string>();
    for (const ev of r.events) {
      if (ev.kind !== "event" || !ev.name) continue;
      const gid = (ev.data as Record<string, unknown> | undefined)?.["gap_id"];
      if (typeof gid !== "string") continue;
      if (ev.name === "gap_validate_start" || ev.name === "gap_rank_start") inFlight.add(gid);
      else if (["gap_validated", "gap_validate_failed", "gap_ranked", "gap_rank_failed"].includes(ev.name)) inFlight.delete(gid);
    }
    inFlight.forEach((gid) => gaps.add(gid));
  }
  return { papers, gaps };
}

export function Sidebar({ onCollapse }: { onCollapse: () => void }) {
  const {
    topics, activeSlug, active, bundle, nav, setNav, setActiveSlug, createTopic, runs,
    sessions, deleteSession, renameSession,
  } = useWorkspace();
  // Right-click target for the session rows. Destructive actions live here rather
  // than on the session's own header, where a stray click is one pixel away from
  // the thing you were reading.
  const [menu, setMenu] = useState<{ id: string; title: string; x: number; y: number } | null>(null);
  const [open, setOpen] = useState({ papers: true, gaps: true, ideas: true, sessions: true });
  const [pickerOpen, setPickerOpen] = useState(false);
  // New-topic creation: inline field in the picker; `creating` shows the field,
  // `newTopic` holds its value, `busy` covers the (few-second) agent run.
  const [creating, setCreating] = useState(false);
  const [newTopic, setNewTopic] = useState("");
  const [creatingBusy, setCreatingBusy] = useState(false);
  const pickerRef = useRef<HTMLDivElement>(null);
  const toggle = (k: keyof typeof open) => setOpen((s) => ({ ...s, [k]: !s[k] }));

  const submitNewTopic = async () => {
    const name = newTopic.trim();
    if (!name || creatingBusy) return;
    setCreatingBusy(true);
    const slug = await createTopic(name);
    setCreatingBusy(false);
    if (slug) {
      setNewTopic("");
      setCreating(false);
      setPickerOpen(false);
    }
  };
  const busy = useMemo(() => busyEntities(runs), [runs]);
  const ideasRunning = runs.some((r) => r.kind === "idea" && r.status === "running");

  useEffect(() => {
    if (!pickerOpen) return;
    const onDoc = (e: MouseEvent) => {
      if (pickerRef.current && !pickerRef.current.contains(e.target as Node)) setPickerOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [pickerOpen]);

  const papers = bundle?.papers ?? [];
  // The nav tree shows only the master gap list — the ranked, live opportunities —
  // ordered by the combined Overall score (highest first). Other stages and
  // archived (duplicate/solved) gaps are reachable from the Gaps list, not here.
  const rankCfg = bundle?.rankingConfig ?? DEFAULT_RANKING_CONFIG;
  const gaps = (bundle?.allGaps ?? bundle?.gaps ?? [])
    .filter((g) => (g.stage ?? g.ingestion?.stage) === "master")
    .sort((a, b) => (overallScore(b, rankCfg) ?? -1) - (overallScore(a, rankCfg) ?? -1));
  const ideas = bundle?.ideas ?? [];
  const isActive = (key: Partial<Nav>) => nav.view === key.view && nav.id === (key.id ?? null);

  return (
    <aside className="sidebar">
      <div className="brand">
        <div className="brand-mark">N</div>
        <div className="brand-text">
          <div className="brand-title">Novadraft</div>
          <div className="brand-sub">Research Ideation</div>
        </div>
        <button className="icon-btn brand-collapse" title="Hide sidebar (⌘\\)"
          onClick={onCollapse}><Icon name="chevrons-left" /></button>
      </div>

      <div className="topic-card-wrap" ref={pickerRef}>
        <div className={"topic-card topic-card--withpicker" + (isActive({ view: "topic" }) ? " topic-card--active" : "")}>
          <button className="topic-card-main" onClick={() => setNav({ view: "topic", id: null })}>
            <div className="topic-card-row">
              <div className="topic-dot" style={{ background: active?.categoryColor || "#5B5BF6" }}></div>
              <div className="topic-card-text">
                <div className="topic-card-title">{active?.shortTitle || "—"}</div>
                <div className="topic-card-cat">{active?.category || ""}</div>
              </div>
            </div>
            <div className="topic-card-stats">
              <span><b>{active?.stats.papers ?? papers.length}</b> papers</span>
              <span className="dot-sep"></span>
              <span><b>{active?.stats.gaps ?? gaps.length}</b> gaps</span>
              <span className="dot-sep"></span>
              <span><b>{active?.stats.ideas ?? 0}</b> ideas</span>
            </div>
          </button>
          <button
            className={"topic-picker-btn" + (pickerOpen ? " topic-picker-btn--open" : "")}
            onClick={() => setPickerOpen(!pickerOpen)}
            title="Switch topic"
            aria-expanded={pickerOpen}
          >
            <Icon name="chevron-down" />
          </button>
        </div>

        {pickerOpen && (
          <div className="topic-picker">
            <div className="topic-picker-label">Switch topic</div>
            {topics.map((t) => (
              <button
                key={t.slug}
                className={"topic-picker-row" + (t.slug === activeSlug ? " topic-picker-row--active" : "")}
                onClick={() => { setActiveSlug(t.slug); setPickerOpen(false); }}
              >
                <span className="topic-picker-dot" style={{ background: t.categoryColor }}></span>
                <span className="topic-picker-text">
                  <span className="topic-picker-title">{t.shortTitle}</span>
                  <span className="topic-picker-cat">{t.category}</span>
                </span>
                <span className="topic-picker-meta">
                  {t.stats.papers}·{t.stats.gaps}·{t.stats.ideas}
                </span>
                {t.slug === activeSlug && <span className="topic-picker-check"><Icon name="check" /></span>}
              </button>
            ))}
            <div className="topic-picker-divider"></div>
            {creating ? (
              <div className="topic-create">
                <input
                  className="topic-create-input"
                  autoFocus
                  placeholder="e.g. Reinforcement Learning"
                  value={newTopic}
                  disabled={creatingBusy}
                  onChange={(e) => setNewTopic(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") { e.preventDefault(); submitNewTopic(); }
                    if (e.key === "Escape") { setCreating(false); setNewTopic(""); }
                  }}
                />
                <button
                  className="btn btn--primary topic-create-go"
                  onClick={submitNewTopic}
                  disabled={creatingBusy || !newTopic.trim()}
                >
                  {creatingBusy ? <span className="sidebar-spin" /> : <Icon name="arrow-right" />}
                  {creatingBusy ? "Researching…" : "Create"}
                </button>
                <div className="topic-create-hint">
                  The category agent researches the field and writes a short description.
                </div>
              </div>
            ) : (
              <button className="topic-picker-row topic-picker-row--new" onClick={() => setCreating(true)}>
                <span className="topic-picker-dot topic-picker-dot--new"><Icon name="plus" /></span>
                <span className="topic-picker-text">
                  <span className="topic-picker-title">New research topic</span>
                  <span className="topic-picker-cat">Define papers, gaps, and ideas</span>
                </span>
              </button>
            )}
          </div>
        )}
      </div>

      <div className="sidebar-scroll">
        <Section
          label="Reference Papers"
          count={papers.length}
          open={open.papers}
          onToggle={() => toggle("papers")}
          headerActive={isActive({ view: "papers" })}
          onHeaderClick={() => setNav({ view: "papers", id: null })}
          headerAction={
            <button className="ghost-btn" title="Add paper"
              onClick={(e) => { e.stopPropagation(); setNav({ view: "papers", id: null }); }}>
              <Icon name="plus" />
            </button>
          }
        >
          {papers.map((p) => (
            <SidebarItem
              key={p.id}
              active={isActive({ view: "paper", id: p.id })}
              onClick={() => setNav({ view: "paper", id: p.id })}
              icon={<Icon name="file-text" />}
              label={p.short}
              drag={{ kind: "paper", id: p.id, label: p.title || p.short }}
              busy={busy.papers.has(p.id)}
              meta={<span className="item-year">{p.year ?? ""}</span>}
            />
          ))}
        </Section>

        <Section
          label="Research Gaps & Directions"
          count={gaps.length}
          open={open.gaps}
          onToggle={() => toggle("gaps")}
          headerActive={isActive({ view: "gaps" })}
          onHeaderClick={() => setNav({ view: "gaps", id: null })}
        >
          {gaps.map((g) => (
            <SidebarItem
              key={g.id}
              active={isActive({ view: "gap", id: g.id })}
              onClick={() => setNav({ view: "gap", id: g.id })}
              icon={<SeverityDot severity={g.severity} />}
              label={g.short}
              drag={{ kind: "gap", id: g.id, label: g.title || g.short }}
              meta={<span className="sidebar-score" title="Overall score">{fmtScore(overallScore(g, rankCfg))}</span>}
              busy={busy.gaps.has(g.id)}
            />
          ))}
        </Section>

        <Section
          label="Research Ideas"
          count={ideas.length}
          open={open.ideas}
          onToggle={() => toggle("ideas")}
          headerActive={isActive({ view: "idea-graph" })}
          onHeaderClick={() => setNav({ view: "idea-graph", id: null })}
          headerHint="Open the idea graph"
          headerAction={
            <button className="ghost-btn" title="Ideas as a list"
              onClick={(e) => { e.stopPropagation(); setNav({ view: "ideas", id: null }); }}>
              <Icon name="list" />
            </button>
          }
        >
          {ideasRunning && (
            <div className="sidebar-note"><span className="sidebar-spin" /> a run is developing ideas…</div>
          )}
          {ideas.map((i) => (
            <SidebarItem
              key={i.id}
              active={isActive({ view: "idea", id: i.id })}
              onClick={() => setNav({ view: "idea", id: i.id })}
              icon={<Icon name="bulb" />}
              label={i.short}
              drag={{ kind: "idea", id: i.id, label: i.title || i.short }}
            />
          ))}
        </Section>

        <Section
          label="Sessions"
          count={sessions.length}
          open={open.sessions}
          onToggle={() => toggle("sessions")}
          headerActive={isActive({ view: "sessions" })}
          onHeaderClick={() => setNav({ view: "sessions", id: null })}
          headerHint="Start a new session"
          headerAction={
            <button className="ghost-btn" title="New session"
              onClick={(e) => { e.stopPropagation(); setNav({ view: "sessions", id: null }); }}>
              <Icon name="plus" />
            </button>
          }
        >
          {sessions.map((sess) => (
            <SidebarItem
              key={sess.session_id}
              active={isActive({ view: "session", id: sess.session_id })}
              onClick={() => setNav({ view: "session", id: sess.session_id })}
              onContextMenu={(e) => {
                e.preventDefault();
                setMenu({ id: sess.session_id, title: sess.title, x: e.clientX, y: e.clientY });
              }}
              icon={<Icon name="msg" />}
              label={sess.title}
              busy={runs.some((r) => r.sessionId === sess.session_id && r.status === "running")}
              meta={<span className="item-year">{sess.contextCount || ""}</span>}
            />
          ))}
        </Section>
      </div>

      {menu && (
        <ContextMenu
          x={menu.x} y={menu.y} onClose={() => setMenu(null)}
          items={[
            {
              label: "Rename…",
              onClick: () => {
                const next = prompt("Session name", menu.title);
                if (next && next.trim()) renameSession(menu.id, next.trim());
              },
            },
            {
              label: "Delete session",
              danger: true,
              onClick: () => {
                if (confirm(`Delete "${menu.title}"? Its transcript and loaded context go with it.`))
                  deleteSession(menu.id);
              },
            },
          ]}
        />
      )}
    </aside>
  );
}

function Section({
  label, count, open, onToggle, headerActive, onHeaderClick, headerAction, headerHint, children,
}: {
  label: string; count: number; open: boolean; onToggle: () => void;
  headerActive: boolean; onHeaderClick: () => void; headerAction?: ReactNode;
  headerHint?: string; children: ReactNode;
}) {
  return (
    <div className="section">
      <div className={"section-header" + (headerActive ? " section-header--active" : "")}>
        <button className="section-chevron" onClick={onToggle}>
          <Icon name={open ? "chevron-down" : "chevron-right"} />
        </button>
        <button className="section-label" onClick={onHeaderClick} title={headerHint}>
          <span>{label}</span>
          <span className="section-count">{count}</span>
        </button>
        {headerAction}
      </div>
      {open && <div className="section-body">{children}</div>}
    </div>
  );
}

function SidebarItem({
  active, onClick, onContextMenu, icon, label, meta, busy, drag,
}: {
  active: boolean; onClick: () => void;
  onContextMenu?: (e: React.MouseEvent) => void;
  icon: ReactNode; label: string; meta?: ReactNode;
  busy?: boolean;
  // Present on papers / gaps / ideas: the row is a drag source, so anything in
  // the nav tree can be dropped straight into a session's composer.
  drag?: DragRef;
}) {
  return (
    <button
      className={"sidebar-item" + (active ? " sidebar-item--active" : "") + (busy ? " sidebar-item--busy" : "")}
      onClick={onClick}
      onContextMenu={onContextMenu}
      {...(drag ? refDragProps(drag) : {})}
    >
      <span className="sidebar-item-icon">{busy ? <span className="sidebar-spin" /> : icon}</span>
      <span className="sidebar-item-label">{label}</span>
      {meta && <span className="sidebar-item-meta">{meta}</span>}
    </button>
  );
}


/** A small right-click menu, positioned at the cursor and closed by anything else.
 *  Rendered inline rather than in a portal: the sidebar is not clipped, and one
 *  fixed-position element does not need the ceremony. */
function ContextMenu({
  x, y, items, onClose,
}: {
  x: number; y: number; onClose: () => void;
  items: { label: string; onClick: () => void; danger?: boolean }[];
}) {
  useEffect(() => {
    const close = () => onClose();
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    // `capture` so the click that dismisses the menu does not also land on
    // whatever is underneath it.
    document.addEventListener("mousedown", close, true);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", close, true);
      document.removeEventListener("keydown", onKey);
    };
  }, [onClose]);

  // Sessions sit at the bottom of the nav tree, so a menu opened on the last one
  // would hang off the viewport; flip it above the cursor when it would not fit.
  const height = items.length * 32 + 10;
  const top = y + height > window.innerHeight - 8 ? Math.max(8, y - height) : y;

  return (
    <div className="ctx-menu" style={{ left: x, top }}>
      {items.map((item) => (
        <button
          key={item.label}
          className={"ctx-menu-item" + (item.danger ? " ctx-menu-item--danger" : "")}
          onMouseDown={(e) => { e.stopPropagation(); }}
          onClick={() => { item.onClick(); onClose(); }}
        >
          {item.label}
        </button>
      ))}
    </div>
  );
}
