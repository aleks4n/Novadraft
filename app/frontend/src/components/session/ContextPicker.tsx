// The **+** button's window: everything in the workspace that can enter a
// session's context, searchable, multi-select.
//
// The same selection the graph panel and drag-and-drop make, for the case those
// are bad at: finding one paper among sixty by name. A paper/note/file already
// loaded is shown but not selectable — loading it twice is a no-op. A gap/idea
// already loaded stays selectable: picking it again tells the agent this turn is
// about it, same as clicking it in the graph.
import { useEffect, useMemo, useState } from "react";
import { Icon } from "../Icon";
import { useWorkspace } from "../../lib/store";
import { buildSearchRows, filterSearchRows, type SearchRow } from "../../lib/searchRows";
import type { ContextKind, ContextRef } from "../../lib/types";

type Row = SearchRow;

export function ContextPicker({
  loaded, staged, onClose, onAdd,
}: {
  loaded: ContextRef[];
  staged: ContextRef[];
  onClose: () => void;
  onAdd: (refs: ContextRef[]) => void;
}) {
  const { bundle } = useWorkspace();
  const [query, setQuery] = useState("");
  const [tab, setTab] = useState<"all" | ContextKind>("all");
  const [picked, setPicked] = useState<Record<string, Row>>({});

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  const rows: Row[] = useMemo(() => buildSearchRows(bundle), [bundle]);

  const alreadyLoaded = new Set(loaded.map((c) => `${c.kind}:${c.id}`));
  const alreadyStaged = new Set(staged.map((c) => `${c.kind}:${c.id}`));

  const shown = filterSearchRows(rows, query, tab);

  const count = Object.keys(picked).length;
  const commit = () => {
    onAdd(Object.values(picked).map((r) => ({
      kind: r.kind, id: r.id, label: r.label, digest: r.sub.slice(0, 400),
    })));
    onClose();
  };

  return (
    <div className="modal-scrim" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="modal" role="dialog" aria-label="Add to context">
        <div className="modal-head">
          <Icon name="search" />
          <input
            className="modal-search" autoFocus value={query} placeholder="Search gaps, papers and ideas…"
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && count) commit(); }}
          />
          <button className="ghost-btn" onClick={onClose}><Icon name="x" /></button>
        </div>

        <div className="modal-tabs">
          {(["all", "gap", "idea", "paper"] as const).map((t) => (
            <button key={t}
              className={"modal-tab" + (tab === t ? " modal-tab--active" : "")}
              onClick={() => setTab(t)}>
              {t === "all" ? "Everything" : t + "s"}
              <span className="modal-tab-count">
                {t === "all" ? rows.length : rows.filter((r) => r.kind === t).length}
              </span>
            </button>
          ))}
        </div>

        <div className="modal-list">
          {shown.length === 0 && <div className="empty-state empty-state--inline">Nothing matches.</div>}
          {shown.map((r) => {
            const k = `${r.kind}:${r.id}`;
            const pinnable = r.kind === "gap" || r.kind === "idea";
            const inStaged = alreadyStaged.has(k);
            const inLoaded = alreadyLoaded.has(k);
            // Already staged: nothing this modal can add. Already loaded: still a no-op
            // for a paper/note/file, but a gap/idea stays pickable — picking it again is
            // how it gets (re)selected as this turn's subject.
            const disabled = inStaged || (inLoaded && !pinnable);
            const on = Boolean(picked[k]);
            return (
              <button
                key={k}
                className={"pick-row" + (on ? " pick-row--on" : "") + (disabled ? " pick-row--in" : "")}
                onClick={() => {
                  if (disabled) return;
                  setPicked((p) => {
                    const next = { ...p };
                    if (next[k]) delete next[k]; else next[k] = r;
                    return next;
                  });
                }}
              >
                <span className={"ctx-kind ctx-kind--" + r.kind}>{r.kind}</span>
                <span className="pick-text">
                  <span className="pick-label">{r.label}</span>
                  <span className="pick-sub">{r.sub}</span>
                </span>
                <span className="pick-mark">
                  {inStaged ? "staged" : disabled ? "in context" : (
                    <>
                      {inLoaded && <span className="pick-loaded-tag">in context</span>}
                      {on ? <Icon name="check" /> : <Icon name="plus" />}
                    </>
                  )}
                </span>
              </button>
            );
          })}
        </div>

        <div className="modal-foot">
          <span className="modal-foot-hint">
            Context is a reference, not a payload — the id and a digest travel; the full
            record is read when a run needs it.
          </span>
          <button className="btn btn--ghost" onClick={onClose}>Cancel</button>
          <button className="btn btn--primary" disabled={!count} onClick={commit}>
            Add {count || ""} to context
          </button>
        </div>
      </div>
    </div>
  );
}
