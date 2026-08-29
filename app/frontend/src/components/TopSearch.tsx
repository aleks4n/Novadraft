// Global search: gaps, papers and ideas of the active topic, one field, one
// dropdown of results. Same row-building + filtering as the session
// ContextPicker (../session/ContextPicker.tsx) — this just navigates to a
// result instead of adding it to context.
import { useEffect, useMemo, useRef, useState } from "react";
import { Icon } from "./Icon";
import { useWorkspace } from "../lib/store";
import { buildSearchRows, filterSearchRows } from "../lib/searchRows";
import type { ContextKind } from "../lib/types";

const KIND_VIEW: Record<ContextKind, "gap" | "idea" | "paper" | null> = {
  gap: "gap", idea: "idea", paper: "paper", file: null, note: null,
};

export function TopSearch() {
  const { bundle, setNav } = useWorkspace();
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  const rows = useMemo(() => buildSearchRows(bundle), [bundle]);
  const shown = useMemo(() => (query.trim() ? filterSearchRows(rows, query).slice(0, 20) : []), [rows, query]);

  useEffect(() => {
    const onDown = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, []);

  const pick = (kind: ContextKind, id: string) => {
    const view = KIND_VIEW[kind];
    if (view) setNav({ view, id });
    setQuery("");
    setOpen(false);
  };

  return (
    <div className="topsearch" ref={rootRef}>
      <Icon name="search" className="topsearch-icon" />
      <input
        className="topsearch-input"
        placeholder="Search gaps, papers, ideas…"
        value={query}
        onChange={(e) => { setQuery(e.target.value); setOpen(true); }}
        onFocus={() => setOpen(true)}
        onKeyDown={(e) => {
          if (e.key === "Escape") { setQuery(""); setOpen(false); (e.target as HTMLInputElement).blur(); }
          if (e.key === "Enter" && shown.length) pick(shown[0].kind, shown[0].id);
        }}
      />
      {query && (
        <button className="topsearch-clear" onClick={() => { setQuery(""); setOpen(false); }}>
          <Icon name="x" />
        </button>
      )}
      {open && query.trim() && (
        <div className="topsearch-results">
          {shown.length === 0 && <div className="empty-state empty-state--inline">Nothing matches.</div>}
          {shown.map((r) => (
            <button key={`${r.kind}:${r.id}`} className="pick-row" onClick={() => pick(r.kind, r.id)}>
              <span className={"ctx-kind ctx-kind--" + r.kind}>{r.kind}</span>
              <span className="pick-text">
                <span className="pick-label">{r.label}</span>
                <span className="pick-sub">{r.sub}</span>
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
