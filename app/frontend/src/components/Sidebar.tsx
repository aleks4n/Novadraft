// Sidebar v2 — workspace brand, topic card with a real topic picker, and
// per-topic Papers / Gaps / Ideas sections fed from the backend.
import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { Icon, SeverityDot, StatusPip } from "./Icon";
import { useWorkspace } from "../lib/store";
import type { Nav } from "../lib/types";

export function Sidebar() {
  const { topics, activeSlug, active, bundle, nav, setNav, setActiveSlug } = useWorkspace();
  const [open, setOpen] = useState({ papers: true, gaps: true, ideas: true });
  const [pickerOpen, setPickerOpen] = useState(false);
  const pickerRef = useRef<HTMLDivElement>(null);
  const toggle = (k: keyof typeof open) => setOpen((s) => ({ ...s, [k]: !s[k] }));

  useEffect(() => {
    if (!pickerOpen) return;
    const onDoc = (e: MouseEvent) => {
      if (pickerRef.current && !pickerRef.current.contains(e.target as Node)) setPickerOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [pickerOpen]);

  const papers = bundle?.papers ?? [];
  const gaps = bundle?.gaps ?? [];
  const ideas = bundle?.ideas ?? [];
  const isActive = (key: Partial<Nav>) => nav.view === key.view && nav.id === (key.id ?? null);

  return (
    <aside className="sidebar">
      <div className="brand">
        <div className="brand-mark">A</div>
        <div className="brand-text">
          <div className="brand-title">AI OS</div>
          <div className="brand-sub">· Research</div>
        </div>
        <button className="icon-btn brand-collapse" title="Collapse"><Icon name="chevrons-left" /></button>
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
            <button className="topic-picker-row topic-picker-row--new">
              <span className="topic-picker-dot topic-picker-dot--new"><Icon name="plus" /></span>
              <span className="topic-picker-text">
                <span className="topic-picker-title">New research topic</span>
                <span className="topic-picker-cat">Define papers, gaps, and ideas</span>
              </span>
            </button>
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
              onClick={(e) => { e.stopPropagation(); setNav({ view: "add-paper", id: null }); }}>
              <Icon name="plus" />
            </button>
          }
        >
          <button
            className={"new-idea-row" + (isActive({ view: "add-paper" }) ? " new-idea-row--active" : "")}
            onClick={() => setNav({ view: "add-paper", id: null })}
          >
            <Icon name="sparkle" /><span>Add paper</span>
          </button>
          {papers.map((p) => (
            <SidebarItem
              key={p.id}
              active={isActive({ view: "paper", id: p.id })}
              onClick={() => setNav({ view: "paper", id: p.id })}
              icon={<Icon name="file-text" />}
              label={p.short}
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
            />
          ))}
        </Section>

        <Section
          label="Research Ideas"
          count={ideas.length}
          open={open.ideas}
          onToggle={() => toggle("ideas")}
          headerActive={isActive({ view: "ideas" })}
          onHeaderClick={() => setNav({ view: "ideas", id: null })}
          headerAction={
            <button className="ghost-btn" title="New ideation"
              onClick={(e) => { e.stopPropagation(); setNav({ view: "new-idea", id: null }); }}>
              <Icon name="plus" />
            </button>
          }
        >
          <button
            className={"new-idea-row" + (isActive({ view: "new-idea" }) ? " new-idea-row--active" : "")}
            onClick={() => setNav({ view: "new-idea", id: null })}
          >
            <Icon name="sparkle" /><span>New ideation</span>
          </button>
          {ideas.map((i) => (
            <SidebarItem
              key={i.id}
              active={isActive({ view: "idea", id: i.id })}
              onClick={() => setNav({ view: "idea", id: i.id })}
              icon={<Icon name="bulb" />}
              label={i.short}
              meta={<StatusPip status={i.status} />}
            />
          ))}
        </Section>
      </div>

      <div className="user-row">
        <div className="avatar avatar--lg" style={{ background: "#C75B5B" }}>DH</div>
        <div className="user-text">
          <div className="user-name">Denny Hermann</div>
          <div className="user-plan">Research · Pro</div>
        </div>
        <button className="icon-btn"><Icon name="settings" /></button>
      </div>
    </aside>
  );
}

function Section({
  label, count, open, onToggle, headerActive, onHeaderClick, headerAction, children,
}: {
  label: string; count: number; open: boolean; onToggle: () => void;
  headerActive: boolean; onHeaderClick: () => void; headerAction?: ReactNode; children: ReactNode;
}) {
  return (
    <div className="section">
      <div className={"section-header" + (headerActive ? " section-header--active" : "")}>
        <button className="section-chevron" onClick={onToggle}>
          <Icon name={open ? "chevron-down" : "chevron-right"} />
        </button>
        <button className="section-label" onClick={onHeaderClick}>
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
  active, onClick, icon, label, meta,
}: {
  active: boolean; onClick: () => void; icon: ReactNode; label: string; meta?: ReactNode;
}) {
  return (
    <button className={"sidebar-item" + (active ? " sidebar-item--active" : "")} onClick={onClick}>
      <span className="sidebar-item-icon">{icon}</span>
      <span className="sidebar-item-label">{label}</span>
      {meta && <span className="sidebar-item-meta">{meta}</span>}
    </button>
  );
}
