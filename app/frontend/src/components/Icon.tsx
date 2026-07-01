// Inline SVG icons — thin-stroke, monoline aesthetic. Ported verbatim from the
// Research Ideation v2 design bundle.
import type { ReactElement } from "react";

const PATHS: Record<string, ReactElement> = {
  settings: <g><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09a1.65 1.65 0 0 0 1.51-1 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33h0a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51h0a1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82v0a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></g>,
  "chevron-down": <polyline points="6 9 12 15 18 9"/>,
  "chevron-right": <polyline points="9 6 15 12 9 18"/>,
  "chevrons-left": <g><polyline points="11 17 6 12 11 7"/><polyline points="18 17 13 12 18 7"/></g>,
  plus: <g><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></g>,
  "file-text": <g><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="8" y1="13" x2="16" y2="13"/><line x1="8" y1="17" x2="14" y2="17"/></g>,
  bulb: <g><path d="M9 18h6"/><path d="M10 22h4"/><path d="M12 2a7 7 0 0 0-4 12.7c.5.4.8 1 .8 1.6V18h6.4v-1.7c0-.6.3-1.2.8-1.6A7 7 0 0 0 12 2z"/></g>,
  sparkle: <g><path d="M12 3l1.8 5.4L19 10l-5.2 1.6L12 17l-1.8-5.4L5 10l5.2-1.6z"/></g>,
  search: <g><circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></g>,
  x: <g><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></g>,
  more: <g><circle cx="5" cy="12" r="1.5"/><circle cx="12" cy="12" r="1.5"/><circle cx="19" cy="12" r="1.5"/></g>,
  star: <polygon points="12 2 15.1 8.3 22 9.3 17 14.1 18.2 21 12 17.8 5.8 21 7 14.1 2 9.3 8.9 8.3"/>,
  send: <g><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></g>,
  attach: <path d="M21.4 11.05 12.25 20.2a6 6 0 1 1-8.49-8.49l9.19-9.19a4 4 0 1 1 5.66 5.66l-9.2 9.19a2 2 0 1 1-2.83-2.83l8.49-8.49"/>,
  external: <g><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></g>,
  git: <g><circle cx="6" cy="18" r="2.5"/><circle cx="6" cy="6" r="2.5"/><circle cx="18" cy="6" r="2.5"/><path d="M6 8.5v7"/><path d="M15.5 7.5 8.5 14.5"/></g>,
  book: <g><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></g>,
  check: <polyline points="20 6 9 17 4 12"/>,
  circle: <circle cx="12" cy="12" r="9"/>,
  play: <polygon points="6 4 20 12 6 20 6 4"/>,
  "arrow-right": <g><line x1="5" y1="12" x2="19" y2="12"/><polyline points="12 5 19 12 12 19"/></g>,
  "arrow-up-right": <g><line x1="7" y1="17" x2="17" y2="7"/><polyline points="7 7 17 7 17 17"/></g>,
  grid: <g><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/></g>,
  list: <g><line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/></g>,
  tag: <g><path d="M20.59 13.41 13.42 20.58a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z"/><line x1="7" y1="7" x2="7.01" y2="7"/></g>,
  filter: <polygon points="22 3 2 3 10 12.46 10 19 14 21 14 12.46 22 3"/>,
  users: <g><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></g>,
  clock: <g><circle cx="12" cy="12" r="9"/><polyline points="12 7 12 12 15 14"/></g>,
  calendar: <g><rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></g>,
  lightning: <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>,
  flask: <g><path d="M9 2v6L3 20a2 2 0 0 0 2 3h14a2 2 0 0 0 2-3L15 8V2"/><line x1="9" y1="2" x2="15" y2="2"/></g>,
  target: <g><circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1.5"/></g>,
  compass: <g><circle cx="12" cy="12" r="9"/><polygon points="16.24 7.76 14.12 14.12 7.76 16.24 9.88 9.88"/></g>,
  share: <g><circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/><line x1="8.59" y1="13.51" x2="15.42" y2="17.49"/><line x1="15.41" y1="6.51" x2="8.59" y2="10.49"/></g>,
  edit: <g><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.12 2.12 0 0 1 3 3L12 15l-4 1 1-4z"/></g>,
  copy: <g><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></g>,
  pen: <g><path d="M12 19l7-7 3 3-7 7-3-3z"/><path d="M18 13l-1.5-7.5L2 2l3.5 14.5L13 18l5-5z"/><path d="M2 2l7.586 7.586"/><circle cx="11" cy="11" r="2"/></g>,
  msg: <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>,
  robot: <g><rect x="3" y="8" width="18" height="12" rx="2"/><circle cx="9" cy="14" r="1.2"/><circle cx="15" cy="14" r="1.2"/><line x1="12" y1="4" x2="12" y2="8"/><circle cx="12" cy="3" r="1"/></g>,
  alert: <g><path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></g>,
  shield: <g><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><polyline points="9 12 11 14 15 10"/></g>,
};

export function Icon({ name, className = "", size = 16 }: { name: string; className?: string; size?: number }) {
  const path = PATHS[name];
  if (!path) return null;
  return (
    <svg
      className={"icon " + className}
      viewBox="0 0 24 24"
      width={size}
      height={size}
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      {path}
    </svg>
  );
}

export function Avatar({ initials, color, size = 22 }: { initials: string; color: string; size?: number }) {
  return (
    <span className="avatar" style={{ background: color, width: size, height: size, fontSize: size * 0.42 }}>
      {initials}
    </span>
  );
}

export function ScoreBar({ value, label, color = "#5B5BF6" }: { value: number; label: string; color?: string }) {
  const pct = Math.round(value * 100);
  return (
    <div className="score-bar">
      <div className="score-bar-head">
        <span>{label}</span>
        <span className="score-val">{pct}</span>
      </div>
      <div className="score-track">
        <div className="score-fill" style={{ width: pct + "%", background: color }}></div>
      </div>
    </div>
  );
}

export function SeverityDot({ severity }: { severity: string }) {
  const colors: Record<string, string> = { high: "#C75B5B", medium: "#D9A04C", low: "#7B9C6B" };
  return <span className="sev-dot" style={{ background: colors[severity] || colors.medium }}></span>;
}

export function StatusPip({ status }: { status: string }) {
  const map: Record<string, { label: string; color: string }> = {
    active: { label: "active", color: "#5B7BC7" },
    draft: { label: "draft", color: "#9A9A95" },
    archived: { label: "archived", color: "#B8B5AC" },
  };
  const s = map[status] || map.draft;
  return <span className="status-pip" style={{ color: s.color, borderColor: s.color + "33" }}>{s.label}</span>;
}
