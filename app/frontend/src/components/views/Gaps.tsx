import { useState } from "react";
import { Icon } from "../Icon";
import { TabButton, ViewHeader } from "../chrome";
import { GapPipeline } from "../PaperPipeline";
import { RunTranscript } from "../RunTranscript";
import { runForTarget, useWorkspace } from "../../lib/store";
import type { Gap, GapEvidence } from "../../lib/types";

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
function gapStageBadge(g: Gap): { label: string; tone: string } {
  const o = g.ingestion?.outcome;
  const st = gapStage(g);
  if (o === "duplicate" || st === "archive") return { label: "Duplicate", tone: "muted" };
  if (o === "solved") return { label: "Solved", tone: "muted" };
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
  const [sort, setSort] = useState<{ key: SortKey; dir: SortDir }>({ key: "type", dir: "asc" });

  const count = (k: string) =>
    k === "all" ? live.length : k === "archive" ? archived.length : live.filter((g) => gapStage(g) === k).length;
  // Show every pipeline stage tab, even empty ones, so the whole flow is visible.
  const tabs = STAGE_TABS;
  const gaps = tab === "archive" ? archived : tab === "all" ? live : live.filter((g) => gapStage(g) === tab);
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
        {/* Archived duplicates — off to the side, and never in the left nav. */}
        {archived.length > 0 && (
          <TabButton active={tab === "archive"} onClick={() => setTab("archive")}
            icon="archived" label={`Archived · ${archived.length}`} />
        )}
      </div>

      {gaps.length === 0 ? (
        <div className="empty-state">
          {tab === "archive"
            ? "No archived gaps — the director parks duplicates here after merging them."
            : all.length === 0
              ? "No gaps yet — they're extracted when a paper is ingested."
              : "No gaps in this stage."}
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
            {sorted.map((g) => {
              const badge = gapStageBadge(g);
              return (
                <tr key={g.id} className="gaps-row" onClick={() => setNav({ view: "gap", id: g.id })}>
                  <td>
                    <span className={"gap-type gap-type--" + g.severity}>{typeLabel(g.type)}</span>
                  </td>
                  <td className="gaps-cell-title">
                    {g.title}
                    <span className={"ingest-badge ingest-badge--" + badge.tone} style={{ marginLeft: 8 }}>{badge.label}</span>
                  </td>
                  <td><span className="gap-status">{statusLabel(g.status)}</span></td>
                  <td className="gaps-cell-num">{g.derivedFrom.length}</td>
                  <td className="gaps-cell-num">{fmtScore(g.novelty)}</td>
                  <td className="gaps-cell-num">{fmtScore(g.feasibility)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
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
