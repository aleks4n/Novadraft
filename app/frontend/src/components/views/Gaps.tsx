import { useEffect, useRef, useState } from "react";
import type { PointerEvent as ReactPointerEvent } from "react";
import { Icon } from "../Icon";
import { TabButton, ViewHeader } from "../chrome";
import { GapPipeline } from "../PaperPipeline";
import { RunTranscript } from "../RunTranscript";
import { runForTarget, useWorkspace } from "../../lib/store";
import type { Gap, GapEvidence, RankingConfig } from "../../lib/types";
import { DEFAULT_RANKING_CONFIG, fmtScore, overallScore, passesFloors } from "../../lib/ranking";

// A gap can still be pushed through ingestion while it sits in these stages.
const RESUMABLE_STAGES = new Set(["pending", "to_validate"]);
function gapResumable(g: Gap): boolean {
  return RESUMABLE_STAGES.has(g.stage ?? g.ingestion?.stage ?? "");
}

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
// The ranking columns read the ranking agent's three dimensions (g.ranking) plus
// the combined "Overall" score (weighted by the config); they stay null (—) until
// a gap has been ranked. New metrics just need a row added here + a <th>.
type SortKey = "type" | "title" | "status" | "sources" | "overall" | "openness" | "impact" | "feasibility";
type SortDir = "asc" | "desc";
type Column = { key: SortKey; label: string; numeric?: boolean; get: (g: Gap) => string | number | null };

// Overall depends on the live weights, so columns are built per-render from cfg.
function buildColumns(cfg: RankingConfig): Column[] {
  return [
    { key: "type", label: "Type", get: (g) => g.type },
    { key: "title", label: "Gap", get: (g) => g.title.toLowerCase() },
    { key: "status", label: "Status", get: (g) => gapStageBadge(g).label },
    { key: "sources", label: "Sources", numeric: true, get: (g) => g.derivedFrom.length },
    { key: "overall", label: "Overall", numeric: true, get: (g) => overallScore(g, cfg) },
    { key: "openness", label: "Openness", numeric: true, get: (g) => g.ranking?.openness ?? null },
    { key: "impact", label: "Impact", numeric: true, get: (g) => g.ranking?.impact ?? null },
    { key: "feasibility", label: "Feasibility", numeric: true, get: (g) => g.ranking?.feasibility ?? null },
  ];
}

// Numeric columns lead with the highest value first — static, independent of cfg.
const NUMERIC_KEYS = new Set<SortKey>(["sources", "overall", "openness", "impact", "feasibility"]);

// Default column widths as fractions of the table (they sum to 1). The table is
// table-layout:fixed, so these are honoured exactly; the resize handles shift
// width between two neighbours, keeping the sum — and thus the total width — fixed.
const DEFAULT_COL_FRAC: Record<SortKey, number> = {
  type: 0.13, title: 0.31, status: 0.12, sources: 0.08,
  overall: 0.09, openness: 0.09, impact: 0.09, feasibility: 0.09,
};
const MIN_COL_FRAC = 0.05; // a column can't be dragged narrower than this

function compareGaps(a: Gap, b: Gap, key: SortKey, dir: SortDir, columns: Column[]): number {
  const col = columns.find((c) => c.key === key)!;
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


const gapStage = (g: Gap): string => g.stage ?? g.ingestion?.stage ?? "pending";

// Stage filter tabs for the gaps list. "archive" is handled as a separate button.
const STAGE_TABS: { key: string; label: string; icon: string }[] = [
  { key: "all", label: "All", icon: "compass" },
  { key: "pending", label: "Pending", icon: "clock" },
  { key: "to_validate", label: "To validate", icon: "search" },
  { key: "to_rank", label: "To rank", icon: "list" },
  { key: "master", label: "Master", icon: "check" },
];

// A short badge for where a gap is (its director/validator outcome, else stage).
// The archive/ stage now holds two kinds of retired gap — director duplicates
// and validator-solved gaps — so distinguish them rather than labelling every
// archived gap "Duplicate".
function gapStageBadge(g: Gap): { label: string; tone: string } {
  const o = g.ingestion?.outcome;
  const st = gapStage(g);
  // A validator-solved gap and a director duplicate both live in archive/, so
  // key off each gap's own markers. Check solved first: the mapper's stage-derived
  // outcome can lag, but verdict.status === "solved" is unambiguous.
  const isSolved = g.verdict?.status === "solved" || o === "solved";
  const isDuplicate = !isSolved && (!!g.duplicateOf || g.status === "duplicate" || o === "duplicate");
  if (isSolved) return { label: "Solved", tone: "muted" };
  if (isDuplicate) return { label: "Duplicate", tone: "muted" };
  if (st === "archive") return { label: "Archived", tone: "muted" };
  if (o === "partially_solved") return { label: "Partial", tone: "warn" };
  if (o === "open") return { label: g.ranking ? "Ranked" : "Open", tone: "ok" };
  if (st === "to_validate") return { label: "To validate", tone: "active" };
  if (st === "to_rank") return { label: "To rank", tone: "active" };
  if (st === "master") return { label: "Master", tone: "ok" };
  return { label: "Pending", tone: "muted" };
}

export function GapsOverview() {
  const { active, bundle, setNav } = useWorkspace();
  const all = bundle?.allGaps ?? bundle?.gaps ?? [];
  const live = all.filter((g) => gapStage(g) !== "archive");
  const archived = all.filter((g) => gapStage(g) === "archive");
  const [tab, setTab] = useState<string>("all");
  // Lead with the strongest opportunities: highest combined score first.
  const [sort, setSort] = useState<{ key: SortKey; dir: SortDir }>({ key: "overall", dir: "desc" });

  // View-time ranking knobs: seeded from the config the backend serves, then
  // tunable live in the controls panel. We re-seed only when the topic changes
  // (via the ref, so background bundle refetches don't clobber the user's edits).
  const served = bundle?.rankingConfig;
  const servedRef = useRef(served);
  servedRef.current = served;
  const [cfg, setCfg] = useState<RankingConfig>(() => served ?? DEFAULT_RANKING_CONFIG);
  const [showControls, setShowControls] = useState(false);
  useEffect(() => {
    if (servedRef.current) setCfg(servedRef.current);
  }, [active?.slug]);

  const columns = buildColumns(cfg);

  // Resizable columns. Widths are fractions of the table that always sum to 1, so
  // dragging a divider moves width between the two neighbouring columns without
  // ever changing the table's total width. tableRef measures the pixel width so a
  // drag delta in px maps to a delta in fraction.
  const tableRef = useRef<HTMLTableElement>(null);
  const [colFrac, setColFrac] = useState<Record<SortKey, number>>(DEFAULT_COL_FRAC);
  function startResize(e: ReactPointerEvent, leftKey: SortKey, rightKey: SortKey) {
    e.preventDefault();
    e.stopPropagation();
    const tableW = tableRef.current?.getBoundingClientRect().width ?? 1;
    const startX = e.clientX;
    const startLeft = colFrac[leftKey];
    const startRight = colFrac[rightKey];
    const pair = startLeft + startRight; // conserved across the drag
    const onMove = (ev: PointerEvent) => {
      const d = (ev.clientX - startX) / tableW;
      const left = Math.min(Math.max(startLeft + d, MIN_COL_FRAC), pair - MIN_COL_FRAC);
      setColFrac((f) => ({ ...f, [leftKey]: left, [rightKey]: pair - left }));
    };
    const onUp = () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      document.body.classList.remove("col-resizing");
    };
    document.body.classList.add("col-resizing");
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  }

  const count = (k: string) =>
    k === "all" ? live.length : k === "archive" ? archived.length : live.filter((g) => gapStage(g) === k).length;
  // Show every pipeline stage tab, even empty ones, so the whole flow is visible.
  const tabs = STAGE_TABS;
  const staged = tab === "archive" ? archived : tab === "all" ? live : live.filter((g) => gapStage(g) === tab);
  // Apply the min-floor filters, then sort. Track how many ranked gaps the floors hid.
  const gaps = staged.filter((g) => passesFloors(g, cfg));
  const hidden = staged.length - gaps.length;
  const sorted = [...gaps].sort((a, b) => compareGaps(a, b, sort.key, sort.dir, columns));

  function onSort(key: SortKey) {
    setSort((s) =>
      s.key === key
        ? { key, dir: s.dir === "asc" ? "desc" : "asc" }
        // numeric columns lead with the most interesting (highest) value first
        : { key, dir: NUMERIC_KEYS.has(key) ? "desc" : "asc" }
    );
  }

  return (
    <div className="view view--list">
      <ViewHeader
        eyebrow={`Research gaps · ${live.length}`}
        title="Research Gaps & Directions"
        subtitle="Open problems each paper states about itself. Each gap flows through the ingestion pipeline — mined, deduplicated, validated against prior art, then ranked."
        breadcrumb={[
          { label: "Research" },
          { label: active?.shortTitle || "Topic", onClick: () => setNav({ view: "topic", id: null }) },
          { label: "Gaps", current: true },
        ]}
      />

      <div className="tabbar">
        {tabs.map((t) => (
          <TabButton key={t.key} active={tab === t.key} onClick={() => setTab(t.key)}
            icon={t.icon} label={`${t.label} · ${count(t.key)}`} />
        ))}
        {/* Archived gaps — duplicates + solved — off to the side, never in the left nav. */}
        {archived.length > 0 && (
          <TabButton active={tab === "archive"} onClick={() => setTab("archive")}
            icon="archived" label={`Archived · ${archived.length}`} />
        )}
        <button
          type="button"
          className={"rank-controls-toggle" + (showControls ? " is-open" : "")}
          onClick={() => setShowControls((v) => !v)}
          title="Tune the ranking weights and filter floors"
        >
          <Icon name="list" className="rank-controls-toggle-icon" />
          Ranking
          {hidden > 0 && <span className="rank-controls-badge">{hidden}</span>}
        </button>
      </div>

      {showControls && (
        <RankingControls cfg={cfg} setCfg={setCfg} served={served} hidden={hidden} />
      )}

      {gaps.length === 0 ? (
        <div className="empty-state">
          {hidden > 0
            ? `All ${hidden} gap${hidden === 1 ? "" : "s"} in this view are hidden by the ranking filters — lower the min floors to see them.`
            : tab === "archive"
            ? "No archived gaps — duplicates and already-solved gaps are parked here."
            : all.length === 0
              ? "No gaps yet — they're extracted when a paper is ingested."
              : "No gaps in this stage."}
        </div>
      ) : (
        <table className="gaps-table" ref={tableRef}>
          <colgroup>
            {columns.map((c) => (
              <col key={c.key} style={{ width: (colFrac[c.key] * 100).toFixed(3) + "%" }} />
            ))}
          </colgroup>
          <thead>
            <tr>
              {columns.map((c, i) => (
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
                  {i < columns.length - 1 && (
                    <span
                      className="gaps-col-resize"
                      onPointerDown={(e) => startResize(e, c.key, columns[i + 1].key)}
                      onClick={(e) => e.stopPropagation()}
                    />
                  )}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sorted.map((g) => {
              const badge = gapStageBadge(g);
              return (
                <tr key={g.id} className="gaps-row" onClick={() => setNav({ view: "gap", id: g.id })}>
                  <td>
                    <span className={"gap-type gap-type--" + g.severity}>{typeLabel(g.type)}</span>
                  </td>
                  <td className="gaps-cell-title">{g.title}</td>
                  <td>
                    <span className={"ingest-badge ingest-badge--" + badge.tone}>{badge.label}</span>
                  </td>
                  <td className="gaps-cell-num">{g.derivedFrom.length}</td>
                  <td className="gaps-cell-num gaps-cell-num--strong">{fmtScore(overallScore(g, cfg))}</td>
                  <td className="gaps-cell-num">{fmtScore(g.ranking?.openness ?? null)}</td>
                  <td className="gaps-cell-num">{fmtScore(g.ranking?.impact ?? null)}</td>
                  <td className="gaps-cell-num">{fmtScore(g.ranking?.feasibility ?? null)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}

// The view-time ranking panel: live sliders for the three dimension weights
// (feed the Overall score's weighted geometric mean) and the three min-floors
// (hide gaps whose dimension falls below). Purely view-time — nothing is written
// back to the agent; Reset returns to the config the backend served.
function RankingControls({ cfg, setCfg, served, hidden }: {
  cfg: RankingConfig;
  setCfg: (updater: (c: RankingConfig) => RankingConfig) => void;
  served?: RankingConfig;
  hidden: number;
}) {
  const set = (key: keyof RankingConfig, v: number) => setCfg((c) => ({ ...c, [key]: v }));
  const WEIGHT_ROWS: { key: keyof RankingConfig; label: string }[] = [
    { key: "wOpenness", label: "Openness" },
    { key: "wImpact", label: "Impact" },
    { key: "wFeasibility", label: "Feasibility" },
  ];
  const FLOOR_ROWS: { key: keyof RankingConfig; label: string }[] = [
    { key: "minOpenness", label: "Openness" },
    { key: "minImpact", label: "Impact" },
    { key: "minFeasibility", label: "Feasibility" },
  ];
  return (
    <div className="rank-controls">
      <div className="rank-controls-group">
        <div className="rank-controls-head">
          <span className="rank-controls-title">Weights</span>
          <span className="rank-controls-hint">geometric mean → Overall</span>
        </div>
        {WEIGHT_ROWS.map((r) => (
          <label key={r.key} className="rank-knob">
            <span className="rank-knob-label">{r.label}</span>
            <input type="range" min={0} max={2} step={0.1}
              value={cfg[r.key]} onChange={(e) => set(r.key, Number(e.target.value))} />
            <span className="rank-knob-val">{cfg[r.key].toFixed(1)}</span>
          </label>
        ))}
      </div>
      <div className="rank-controls-group">
        <div className="rank-controls-head">
          <span className="rank-controls-title">Min filters</span>
          <span className="rank-controls-hint">{hidden > 0 ? `${hidden} hidden` : "hide below"}</span>
        </div>
        {FLOOR_ROWS.map((r) => (
          <label key={r.key} className="rank-knob">
            <span className="rank-knob-label">{r.label}</span>
            <input type="range" min={0} max={1} step={0.05}
              value={cfg[r.key]} onChange={(e) => set(r.key, Number(e.target.value))} />
            <span className="rank-knob-val">{cfg[r.key].toFixed(2)}</span>
          </label>
        ))}
      </div>
      <button type="button" className="rank-controls-reset"
        onClick={() => setCfg(() => served ?? DEFAULT_RANKING_CONFIG)}>
        Reset
      </button>
    </div>
  );
}

export function GapDetail({ id }: { id: string }) {
  const { bundle, setNav, runs, startGapResume } = useWorkspace();
  const g: Gap | undefined =
    bundle?.allGaps?.find((x) => x.id === id)
    || bundle?.gaps.find((x) => x.id === id)
    || bundle?.masterGaps.find((x) => x.id === id);
  if (!g) return <div className="empty-state">Gap not found.</div>;
  const papers = bundle?.papers ?? [];
  const sources = g.derivedFrom.map((pid) => papers.find((p) => p.id === pid)).filter(Boolean);
  // The latest run targeting this gap (a resume) streams inline on this page;
  // long-finished runs restored from the server index (no events) stay hidden.
  const lastRun = runForTarget(runs, "gap", g.id);
  const liveRun = lastRun && (lastRun.status !== "done" || lastRun.events.length > 0) ? lastRun : undefined;
  const resuming = liveRun?.status === "running";
  const canResume = gapResumable(g);

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
        actions={
          <>
            {canResume && (
              <button className="btn btn--ghost" onClick={() => startGapResume(g.id)} disabled={resuming}>
                <Icon name={resuming ? "clock" : "play"} /> {resuming ? "Resuming…" : "Resume ingestion"}
              </button>
            )}
            <button className="btn btn--primary"><Icon name="sparkle" /> Spark an idea</button>
          </>
        }
        breadcrumb={[
          { label: "Research" },
          { label: "Gaps", onClick: () => setNav({ view: "gaps", id: null }) },
          { label: g.short, current: true },
        ]}
      />

      {/* Where this gap is in the ingestion pipeline — a strip at the top, exactly
          like the paper page's lifecycle strip. */}
      {g.ingestion?.steps?.length ? (
        <div className="pipeline-strip">
          <GapPipeline steps={g.ingestion.steps} />
          {g.ingestion.outcome === "duplicate" && g.duplicateOf && (
            <div className="pipeline-note">
              Duplicate — evidence merged into{" "}
              <button className="inline-link" onClick={() => setNav({ view: "gap", id: g.duplicateOf! })}>
                <code>{g.duplicateOf}</code>
              </button>
            </div>
          )}
        </div>
      ) : null}

      {liveRun && <RunTranscript run={liveRun} />}

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

        {/* The validator's judgment: has prior art since addressed this gap? */}
        {g.verdict && g.verdict.status && (
          <div className="card card--wide">
            <div className="card-label">Validator verdict</div>
            <p className="card-body">
              <span className={"ingest-badge ingest-badge--" + (g.verdict.status === "solved" ? "muted" : g.verdict.status === "partially_solved" ? "warn" : "ok")}>
                {g.verdict.status.replace(/_/g, " ")}
              </span>
              {g.verdict.confidence != null && <> · confidence {Math.round(g.verdict.confidence * 100)}%</>}
            </p>
            {g.verdict.reasoning && <p className="card-body">{g.verdict.reasoning}</p>}
            {g.verdict.addressedBy.length > 0 && (
              <div className="link-list">
                {g.verdict.addressedBy.map((a, i) => (
                  <div key={i} className="evidence">
                    <div className="evidence-quote">
                      {a.url ? <a href={a.url} target="_blank" rel="noreferrer">{a.title || a.paperId}</a> : (a.title || a.paperId)}
                      {a.relevance && <span className="evidence-section"> · {a.relevance.replace(/_/g, " ")}</span>}
                    </div>
                    {a.contribution && <div className="evidence-meta">{a.contribution}</div>}
                    {a.remainsOpen && <div className="evidence-meta">Still open: {a.remainsOpen}</div>}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* The ranking agent's dimension scores (open opportunities only). */}
        {g.ranking && (
          <div className="card">
            <div className="card-label">Ranking</div>
            <div className="kv-grid">
              <div className="kv"><span>Openness</span><b>{fmtScore(g.ranking.openness)}</b></div>
              <div className="kv"><span>Impact</span><b>{fmtScore(g.ranking.impact)}</b></div>
              <div className="kv"><span>Feasibility</span><b>{fmtScore(g.ranking.feasibility)}</b></div>
            </div>
            {g.ranking.feasibilityReasoning && <p className="card-body">{g.ranking.feasibilityReasoning}</p>}
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
