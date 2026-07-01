import { useState } from "react";
import { Icon } from "../Icon";
import { TabButton, ViewHeader } from "../chrome";
import { useWorkspace } from "../../lib/store";
import type { Gap, GapEvidence } from "../../lib/types";

// The gap agent's own classification — the real signal. Severity is derived from
// it (mappers.py) and only used as the accent colour, not as a separate score.
function typeLabel(t: string) {
  return t.replace(/_/g, " ");
}

// status is a free string on the record; default "unchecked" = not yet triaged.
function statusLabel(s: string) {
  return s === "unchecked" ? "needs review" : s.replace(/_/g, " ");
}

const GROUNDING: Record<string, { label: string; cls: string }> = {
  matched: { label: "grounded", cls: "ground--ok" },
  ambiguous: { label: "ambiguous", cls: "ground--warn" },
  not_found: { label: "ungrounded", cls: "ground--bad" },
};

function GroundingChip({ g }: { g: GapEvidence["grounding"] }) {
  if (!g) return null;
  const m = GROUNDING[g];
  if (!m) return null;
  return <span className={"ground-chip " + m.cls}>{m.label}</span>;
}

// Columns the table can be sorted by. `get` returns a sortable value; numeric
// columns return null when the metric isn't computed yet so they sort last.
// New metrics (novelty, feasibility, …) just need a row added here + a <th>.
type SortKey = "type" | "title" | "status" | "sources" | "novelty" | "feasibility";
type SortDir = "asc" | "desc";

const COLUMNS: { key: SortKey; label: string; numeric?: boolean; get: (g: Gap) => string | number | null }[] = [
  { key: "type", label: "Type", get: (g) => g.type },
  { key: "title", label: "Gap", get: (g) => g.title.toLowerCase() },
  { key: "status", label: "Status", get: (g) => g.status },
  { key: "sources", label: "Sources", numeric: true, get: (g) => g.derivedFrom.length },
  { key: "novelty", label: "Novelty", numeric: true, get: (g) => g.novelty },
  { key: "feasibility", label: "Feasibility", numeric: true, get: (g) => g.feasibility },
];

function compareGaps(a: Gap, b: Gap, key: SortKey, dir: SortDir): number {
  const col = COLUMNS.find((c) => c.key === key)!;
  const va = col.get(a);
  const vb = col.get(b);
  // Nulls (uncomputed metrics) always sink to the bottom regardless of dir.
  if (va === null && vb === null) return 0;
  if (va === null) return 1;
  if (vb === null) return -1;
  let cmp: number;
  if (typeof va === "number" && typeof vb === "number") cmp = va - vb;
  else cmp = String(va).localeCompare(String(vb));
  return dir === "asc" ? cmp : -cmp;
}

function fmtScore(v: number | null): string {
  return v === null ? "—" : v.toFixed(2);
}

export function GapsOverview() {
  const { active, bundle, setNav } = useWorkspace();
  const pending = bundle?.gaps ?? [];
  const master = bundle?.masterGaps ?? [];
  // Pending = gaps freshly mined per-paper (what we have today); Master = the
  // curated, deduplicated list a later phase promotes them into.
  const [tab, setTab] = useState<"pending" | "master">("pending");
  const [sort, setSort] = useState<{ key: SortKey; dir: SortDir }>({ key: "type", dir: "asc" });
  const gaps = tab === "pending" ? pending : master;
  const sorted = [...gaps].sort((a, b) => compareGaps(a, b, sort.key, sort.dir));

  function onSort(key: SortKey) {
    setSort((s) =>
      s.key === key
        ? { key, dir: s.dir === "asc" ? "desc" : "asc" }
        // numeric columns lead with the most interesting (highest) value first
        : { key, dir: COLUMNS.find((c) => c.key === key)?.numeric ? "desc" : "asc" }
    );
  }

  return (
    <div className="view view--list">
      <ViewHeader
        eyebrow={`Research gaps · ${pending.length + master.length}`}
        title="Research Gaps & Directions"
        subtitle="Open problems each paper states about itself — limitations, future work, caveats, and threats to validity. Every gap is typed by the agent and cites the paper it came from."
        actions={<button className="btn btn--ghost"><Icon name="filter" /> Filter</button>}
        breadcrumb={[
          { label: "Research" },
          { label: active?.shortTitle || "Topic", onClick: () => setNav({ view: "topic", id: null }) },
          { label: "Gaps", current: true },
        ]}
      />

      <div className="tabbar">
        <TabButton active={tab === "pending"} onClick={() => setTab("pending")}
          icon="compass" label={`Pending · ${pending.length}`} />
        <TabButton active={tab === "master"} onClick={() => setTab("master")}
          icon="list" label={`Master list · ${master.length}`} />
      </div>

      {tab === "pending" && pending.length === 0 ? (
        <div className="empty-state">No gaps yet — they're extracted when a paper is ingested.</div>
      ) : tab === "master" && master.length === 0 ? (
        <div className="empty-state">
          The master gap list is empty. Pending gaps are promoted here once they're
          reviewed and deduplicated into a single canonical set.
        </div>
      ) : (
        <table className="gaps-table">
          <thead>
            <tr>
              {COLUMNS.map((c) => (
                <th
                  key={c.key}
                  className={
                    "gaps-th" +
                    (c.numeric ? " gaps-th--num" : "") +
                    (sort.key === c.key ? " gaps-th--active" : "")
                  }
                  onClick={() => onSort(c.key)}
                >
                  <span className="gaps-th-inner">
                    {c.label}
                    <Icon
                      name="chevron-down"
                      className={
                        "gaps-sort-caret" +
                        (sort.key === c.key ? " is-active" : "") +
                        (sort.key === c.key && sort.dir === "asc" ? " is-asc" : "")
                      }
                    />
                  </span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sorted.map((g) => (
              <tr key={g.id} className="gaps-row" onClick={() => setNav({ view: "gap", id: g.id })}>
                <td>
                  <span className={"gap-type gap-type--" + g.severity}>{typeLabel(g.type)}</span>
                </td>
                <td className="gaps-cell-title">{g.title}</td>
                <td><span className="gap-status">{statusLabel(g.status)}</span></td>
                <td className="gaps-cell-num">{g.derivedFrom.length}</td>
                <td className="gaps-cell-num">{fmtScore(g.novelty)}</td>
                <td className="gaps-cell-num">{fmtScore(g.feasibility)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

export function GapDetail({ id }: { id: string }) {
  const { bundle, setNav } = useWorkspace();
  const g: Gap | undefined =
    bundle?.gaps.find((x) => x.id === id) || bundle?.masterGaps.find((x) => x.id === id);
  if (!g) return <div className="empty-state">Gap not found.</div>;
  const papers = bundle?.papers ?? [];
  const sources = g.derivedFrom.map((pid) => papers.find((p) => p.id === pid)).filter(Boolean);

  return (
    <div className="view view--detail">
      <ViewHeader
        eyebrow={
          <span>
            <span className={"gap-type gap-type--" + g.severity}>{typeLabel(g.type)}</span>
            {" · "}
            <span className="gap-status">{statusLabel(g.status)}</span>
            {g.extractedAt && <>{" · "}<span>extracted {g.extractedAt}</span></>}
          </span>
        }
        title={g.title}
        actions={<button className="btn btn--primary"><Icon name="sparkle" /> Spark an idea</button>}
        breadcrumb={[
          { label: "Research" },
          { label: "Gaps", onClick: () => setNav({ view: "gaps", id: null }) },
          { label: g.short, current: true },
        ]}
      />

      <div className="view-grid view-grid--paper">
        <div className="card card--wide">
          <div className="card-label">Description</div>
          <p className="card-body">{g.description}</p>
        </div>

        {g.canonicalDescription && (
          <div className="card card--wide">
            <div className="card-label">Canonical form</div>
            <p className="card-body">{g.canonicalDescription}</p>
          </div>
        )}

        {sources.length > 0 && (
          <div className="card">
            <div className="card-label">Derived from</div>
            <div className="link-list">
              {sources.map((p) => (
                <button key={p!.id} className="link-row" onClick={() => setNav({ view: "paper", id: p!.id })}>
                  <Icon name="file-text" />
                  <span>{p!.short}</span>
                  <span className="link-row-meta">{p!.year ?? ""}</span>
                  <Icon name="arrow-up-right" className="link-row-go" />
                </button>
              ))}
            </div>
          </div>
        )}

        {g.evidence.length > 0 && (
          <div className="card card--wide">
            <div className="card-label">Evidence (verbatim from the paper)</div>
            {g.evidence.map((e, i) => (
              <blockquote key={i} className="evidence">
                <div className="evidence-quote">“{e.quote}”</div>
                <div className="evidence-meta">
                  {e.section && <span className="evidence-section">§ {e.section}</span>}
                  <GroundingChip g={e.grounding} />
                </div>
              </blockquote>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
