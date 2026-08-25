// The **+** button's window: everything in the workspace that can enter a
// session's context, searchable, multi-select.
//
// It is the same selection the graph panel and drag-and-drop make — this is just
// the path for the case those two are bad at: finding one paper among sixty by
// name. Items already loaded are shown but not selectable; loading something
// twice is a no-op, and saying so is clearer than silently dropping the click.
import { useEffect, useMemo, useState } from "react";
import { Icon } from "../Icon";
import { useWorkspace } from "../../lib/store";
import { graphGaps } from "../../lib/graphdata";
import type { ContextKind, ContextRef } from "../../lib/types";

interface Row {
  kind: ContextKind;
  id: string;
  label: string;
  sub: string;
}

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

  const rows: Row[] = useMemo(() => {
    if (!bundle) return [];
    return [
      ...graphGaps(bundle).map((g) => ({
        kind: "gap" as const, id: g.id, label: g.title || g.short,
        sub: g.canonicalDescription || g.description,
      })),
      ...bundle.ideas.map((i) => ({
        kind: "idea" as const, id: i.id, label: i.title || i.short,
        sub: i.oneLiner || i.description,
      })),
      ...bundle.papers.map((p) => ({
        kind: "paper" as const, id: p.id, label: p.title || p.short,
        sub: [p.authors, p.year].filter(Boolean).join(" · "),
      })),
    ];
  }, [bundle]);

  const alreadyIn = new Set([
    ...loaded.map((c) => `${c.kind}:${c.id}`),
    ...staged.map((c) => `${c.kind}:${c.id}`),
  ]);

  const q = query.trim().toLowerCase();
  const shown = rows.filter((r) =>
    (tab === "all" || r.kind === tab) &&
    (!q || r.label.toLowerCase().includes(q) || r.id.toLowerCase().includes(q) ||
      r.sub.toLowerCase().includes(q)));

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
            const disabled = alreadyIn.has(k);
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
                  {disabled ? "in context" : on ? <Icon name="check" /> : <Icon name="plus" />}
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
