import { useEffect, useRef, useState } from "react";
import { Icon } from "../Icon";
import { TabButton, ViewHeader } from "../chrome";
import { PaperPipeline } from "../PaperPipeline";
import { RunTranscript } from "../RunTranscript";
import { SourceDropZone } from "../SourceDropZone";
import { runForTarget, useWorkspace } from "../../lib/store";
import { api } from "../../lib/api";
import type { Gap, Paper, RunState } from "../../lib/types";

export const SUGGESTIONS = [
  // --- One example per input query type ---
  { q: "1706.03762", label: "Attention Is All You Need", from: "arXiv ID", note: "Query type: arXiv ID" },
  { q: "https://arxiv.org/abs/2010.11929", label: "An Image is Worth 16x16 Words", from: "arXiv URL", note: "Query type: arXiv URL" },
  { q: "10.48550/arXiv.1810.04805", label: "BERT", from: "DOI", note: "Query type: DOI" },
  { q: "Deep Residual Learning for Image Recognition", label: "ResNet", from: "Paper title", note: "Query type: free-text title" },
  { q: "https://proceedings.mlr.press/v37/ioffe15.pdf", label: "Batch Normalization", from: "PDF URL", note: "Query type: direct PDF URL" },

  // --- Different paper types ---
  { q: "2203.02155", label: "InstructGPT (conference paper)", from: "arXiv ID", note: "Paper type: conference paper" },
  { q: "10.1038/nature14539", label: "Deep Learning (Nature review)", from: "DOI", note: "Paper type: journal review article" },
  { q: "10.6028/NIST.SP.800-207", label: "Zero Trust Architecture", from: "DOI", note: "Paper type: technical report / standard" },
  { q: "Distilling the Knowledge in a Neural Network", label: "Knowledge Distillation (workshop)", from: "Paper title", note: "Paper type: workshop paper" },

  // --- PDF-only: no arXiv record, parsed straight from the PDF ---
  { q: "https://www.cs.toronto.edu/~hinton/absps/JMLRdropout.pdf", label: "Dropout (PDF only, no arXiv)", from: "PDF URL", note: "Edge case: PDF only — no arXiv ID" },
  { q: "10.1145/3292500.3330701", label: "ACM paper (PDF behind publisher)", from: "DOI", note: "Edge case: PDF only — resolve via DOI" },

  // --- No download access: metadata resolves but full text is paywalled / gated ---
  { q: "10.1126/science.aaa8415", label: "Machine learning (Science, paywalled)", from: "DOI", note: "Edge case: no download access — paywalled" },
  { q: "10.1109/5.726791", label: "LeNet (IEEE, gated)", from: "DOI", note: "Edge case: no download access — gated full text" },
];

// A short badge for where a gap is in the ingestion pipeline — the outcome of the
// director/validator (falls back to the raw stage folder for gaps still in flight).
function gapIngestionBadge(g: Gap): { label: string; tone: string } | null {
  const outcome = g.ingestion?.outcome;
  const stage = g.stage ?? g.ingestion?.stage ?? null;
  if (outcome === "duplicate") return { label: "Duplicate", tone: "muted" };
  if (outcome === "solved") return { label: "Already solved", tone: "muted" };
  if (outcome === "partially_solved") return { label: "Partly solved", tone: "warn" };
  if (outcome === "open") return { label: g.ranking ? "Ranked opportunity" : "Open opportunity", tone: "ok" };
  if (outcome === "novel" || stage === "to_validate") return { label: "Validating", tone: "active" };
  if (stage === "pending") return { label: "Mined", tone: "muted" };
  if (stage === "master") return { label: "In master list", tone: "ok" };
  return null;
}

function statusClass(s: string) {
  return (
    "paper-status" +
    (s === "complete"
      ? " paper-status--complete"
      : s === "verified" || s === "summarized"
        ? " paper-status--summarized"
        : "")
  );
}

// One row of the ingest queue: a query that hasn't become a paper yet. While a
// run is fetching it spins here; the moment it attaches to a paper the row
// disappears (the process continues on the paper's page, spinner in the nav).
// Paused/failed queries stay with pick / resume / delete controls.
function IngestQueueRow({ run }: { run: RunState }) {
  const { startPaperRun, cancelRun, restartRun, deleteRun, isCancelling } = useWorkspace();
  const running = run.status === "running";
  const cancelling = running && isCancelling(run.id);

  const statusLabel =
    cancelling ? "Cancelling…"
      : running ? "Fetching…"
      : run.status === "ambiguous" ? "No exact match — pick the paper you meant"
        : run.status === "cancelled" ? "Cancelled"
          : run.status === "interrupted" ? "Interrupted (backend restarted)"
            : run.status === "error" ? `Failed — ${run.error || "unknown error"}`
              : "Finished";

  return (
    <div className={"ingestq-row" + (run.status === "error" ? " ingestq-row--error" : "")}>
      <div className="ingestq-main">
        <span className="ingestq-icon">
          {running ? <span className="sidebar-spin" />
            : run.status === "ambiguous" ? <Icon name="search" />
              : run.status === "error" ? <Icon name="alert" />
                : <Icon name="clock" />}
        </span>
        <span className="ingestq-query" title={run.query || ""}>{run.query}</span>
        <span className="ingestq-status">{statusLabel}</span>
        <span className="ingestq-actions">
          {running && (
            <button className="btn btn--ghost btn--sm" onClick={() => cancelRun(run.id)} disabled={cancelling}>
              <Icon name={cancelling ? "clock" : "x"} /> {cancelling ? "Cancelling…" : "Cancel"}
            </button>
          )}
          {(run.status === "cancelled" || run.status === "interrupted" || run.status === "error") && (
            <button className="btn btn--ghost btn--sm" onClick={() => restartRun(run.id)}>
              <Icon name="play" /> Resume
            </button>
          )}
          {!running && (
            <button className="btn btn--ghost btn--sm" title="Remove this query"
              onClick={() => deleteRun(run.id)}>
              <Icon name="archived" /> Delete
            </button>
          )}
        </span>
      </div>

      {/* Ambiguous → inline candidate picker; picking re-runs the query with the
          candidate's identifier and removes this row. */}
      {run.status === "ambiguous" && (
        <div className="run-candidates run-candidates--queue">
          {run.candidates.map((c, i) => {
            const id = c.arxiv_id || c.doi || c.pdf_url;
            const idLabel = c.arxiv_id ? `arXiv:${c.arxiv_id}`
              : c.doi ? `doi:${c.doi}`
                : c.pdf_url ? "PDF source" : "no identifier";
            return (
              <button
                key={i}
                className="candidate-card"
                disabled={!id}
                title={id ? "Re-run the search with this paper" : "No fetchable identifier"}
                onClick={() => { if (id) { startPaperRun(id); deleteRun(run.id); } }}
              >
                <div className="candidate-title">{c.title || "Untitled"}</div>
                <div className="candidate-meta">
                  {(c.authors?.slice(0, 3).join(", ")) || "Unknown authors"}
                  {c.year ? ` · ${c.year}` : ""}
                </div>
                <div className="candidate-id">{idLabel}{c.source ? ` · ${c.source}` : ""}</div>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

export function PapersOverview() {
  const { active, activeSlug, bundle, setNav, runs, startPaperRun } = useWorkspace();
  const papers = bundle?.papers ?? [];
  const [query, setQuery] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [tryOpen, setTryOpen] = useState(false);
  const tryRef = useRef<HTMLDivElement>(null);
  // The ingest queue: paper runs that haven't attached to a paper yet. As soon
  // as a run learns its paper id it leaves this list (its process streams on the
  // paper's page; the nav shows the spinner). Any number can be live at once.
  const queue = runs.filter((r) => r.kind === "paper" && !r.target);

  // One query per non-empty line — so pasting or Shift+Enter-building a list adds
  // several at once. The field grows with the content up to a cap.
  const queries = query.split("\n").map((q) => q.trim()).filter(Boolean);
  const rows = Math.min(Math.max(query.split("\n").length, 1), 8);

  useEffect(() => {
    if (!tryOpen) return;
    const onDoc = (e: MouseEvent) => {
      if (tryRef.current && !tryRef.current.contains(e.target as Node)) setTryOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [tryOpen]);

  // Enter ingests every non-empty line; a single line auto-opens its paper, a
  // batch stays on this page so the queue stays in view. Shift+Enter → newline.
  const submit = async () => {
    if (!queries.length || submitting) return;
    if (!activeSlug) {
      setSubmitError("Select or create a research topic before ingesting a reference paper.");
      return;
    }
    setSubmitting(true);
    setSubmitError(null);
    const autoOpen = queries.length === 1;
    const accepted = await Promise.all(queries.map((q) => startPaperRun(q, { autoOpen })));
    const failed = queries.filter((_, i) => !accepted[i]);
    setQuery(failed.join("\n"));
    if (failed.length) {
      setSubmitError(
        failed.length === 1
          ? "Could not start ingestion. Check that the backend is running and try again."
          : `Could not start ${failed.length} ingestions. Check that the backend is running and try again.`,
      );
    }
    setSubmitting(false);
  };

  return (
    <div className="view view--list">
      <ViewHeader
        eyebrow={`Reference papers · ${papers.length}`}
        title="Reference Papers"
        subtitle="Papers ingested into this knowledge base. Each is fetched, parsed, summarized & verified, then mined for the research gaps it states — the bar shows how far that pipeline got."
        breadcrumb={[
          { label: "Research" },
          { label: active?.shortTitle || "Topic", onClick: () => setNav({ view: "topic", id: null }) },
          { label: "Papers", current: true },
        ]}
      />

      {/* Inline ingestion: type an arXiv ID / URL / DOI / title and press Enter,
          or add several (Shift+Enter or paste one per line) to ingest a batch in
          parallel. The field grows with what you type. */}
      <div className="papers-ingest">
        <div className="papers-ingest-box">
          <textarea
            className="papers-ingest-textarea"
            rows={rows}
            placeholder="arXiv ID, URL, DOI, or paper title — one per line to add several"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              // Enter ingests; Shift+Enter (or ⌘/Ctrl+Enter) inserts a newline.
              if (e.key === "Enter" && !e.shiftKey && !e.metaKey && !e.ctrlKey) {
                e.preventDefault();
                void submit();
              }
            }}
          />
          <div className="papers-ingest-actions">
            <div className="papers-ingest-try" ref={tryRef}>
              <button
                className={"btn btn--ghost" + (tryOpen ? " btn--active" : "")}
                onClick={() => setTryOpen((v) => !v)}
                aria-expanded={tryOpen}
              >
                <Icon name="compass" /> Try one of these <Icon name="chevron-down" />
              </button>
              {tryOpen && (
                <div className="ingest-menu">
                  <div className="ingest-menu-label">Try one of these</div>
                  <div className="ingest-menu-scroll">
                    {SUGGESTIONS.map((s, i) => (
                      <button
                        key={i}
                        className="ingest-menu-row"
                        onClick={() => {
                          // Append to the list when the user is building one.
                          setQuery((q) => (q.trim() ? q.replace(/\s*$/, "") + "\n" + s.q : s.q));
                          setTryOpen(false);
                        }}
                      >
                        <span className="ingest-menu-text">
                          <span className="ingest-menu-title">{s.label}</span>
                          <span className="ingest-menu-note">{s.note}</span>
                        </span>
                        <span className="ingest-menu-from"><Icon name="compass" /> {s.from}</span>
                      </button>
                    ))}
                  </div>
                </div>
              )}
            </div>
            <span className="papers-ingest-hint">
              {queries.length > 1 ? `${queries.length} papers — ingest in parallel` : "↵ to ingest · ⇧↵ for a new line"}
            </span>
            <button type="button" className="btn btn--primary" onClick={() => void submit()} disabled={!queries.length || submitting}>
              <Icon name="arrow-right" /> {submitting ? "Starting…" : queries.length > 1 ? `Ingest ${queries.length}` : "Ingest"}
            </button>
          </div>
        </div>
        {submitError && <div className="notice notice--warn" role="alert">{submitError}</div>}

        {/* The ingest queue: every query still resolving (or waiting on you).
            Rows disappear as their paper appears in the tree on the left. */}
        {queue.length > 0 && (
          <div className="ingestq">
            {queue.map((r) => <IngestQueueRow key={r.id} run={r} />)}
          </div>
        )}
      </div>

      {papers.length === 0 ? (
        <div className="empty-state">No papers yet. Use the <b>ingest</b> bar above to add one.</div>
      ) : (
        <div className="papers-list">
          {papers.map((p, idx) => (
            <button key={p.id} className="paper-row" onClick={() => setNav({ view: "paper", id: p.id })}>
              <div className="paper-row-num">{idx + 1}</div>
              <div className="paper-row-main">
                <div className="paper-row-title">{p.title}</div>
                <div className="paper-row-meta">
                  <span>{p.authors}</span>
                  <span className="dot-sep"></span>
                  <span>{p.venue}{p.year ? ` · ${p.year}` : ""}</span>
                </div>
                <div className="tag-row">
                  {p.tags.map((t) => <span key={t} className="tag">{t}</span>)}
                  {p.gapCount > 0 && (
                    <span className="tag tag--gap"><Icon name="target" /> {p.gapCount} gap{p.gapCount === 1 ? "" : "s"}</span>
                  )}
                  {p.warnings.length > 0 && (
                    <span className="tag tag--warn" title={p.warnings.join("\n")}>
                      <Icon name="alert" /> {p.warnings.length}
                    </span>
                  )}
                </div>
              </div>
              <div className="paper-row-pipe">
                <PaperPipeline stages={p.stages} compact />
                <span className={statusClass(p.status)}>{p.status}</span>
              </div>
              <div className="paper-row-arrow"><Icon name="arrow-right" /></div>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export function PaperDetail({ id }: { id: string }) {
  const { bundle, activeSlug, setNav, runs, startPaperRun, startPaperGapsResume, uploadSource } = useWorkspace();
  const [paper, setPaper] = useState<Paper | null>(null);
  const [tab, setTab] = useState<"paper" | "survey" | "gaps">("paper");
  const [showWarn, setShowWarn] = useState(false);
  const [uploading, setUploading] = useState(false);

  // The latest run about this paper — an ingest that attached to it (kind
  // "paper") or a gaps-resume scoped to it (kind "resume"). Its full process
  // streams right here on the paper page. A long-finished run restored from the
  // server index (no events) stays hidden — the pipeline strip covers it.
  const lastRun = runForTarget(runs, "paper", id);
  const liveRun = lastRun && (lastRun.status !== "done" || lastRun.events.length > 0) ? lastRun : undefined;
  const running = liveRun?.status === "running";

  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;
    setPaper(null);
    const load = () => {
      if (!activeSlug || cancelled) return;
      api.paper(activeSlug, id)
        .then((p) => { if (!cancelled) setPaper(p); })
        // Mid-ingest the record may not exist yet (we navigate here right after
        // the fetch stage reports the id) — retry until it lands.
        .catch(() => { if (!cancelled) timer = window.setTimeout(load, 1500); });
    };
    load();
    return () => { cancelled = true; if (timer) window.clearTimeout(timer); };
  }, [activeSlug, id]);

  // While a run for this paper is live, keep the record fresh (stages tick as the
  // agent progresses); one final re-fetch when it finishes.
  useEffect(() => {
    if (!liveRun || !activeSlug) return;
    if (liveRun.status === "running") {
      const t = window.setInterval(() => {
        api.paper(activeSlug, id).then(setPaper).catch(() => {});
      }, 2500);
      return () => window.clearInterval(t);
    }
    api.paper(activeSlug, id).then(setPaper).catch(() => {});
  }, [liveRun?.status, liveRun, activeSlug, id]);

  if (!paper) return <div className="empty-state">Loading paper…</div>;
  const survey = paper.survey;
  // Gaps mined from this paper, across *every* stage (they move out of `pending`
  // as the director/validator process them), so the tab keeps showing them with
  // their current ingestion state rather than emptying out.
  const linkedGaps = (bundle?.allGaps ?? bundle?.gaps ?? []).filter((g) => g.derivedFrom.includes(paper.id));
  const doneStages = paper.stages.filter((s) => s.done).length;
  const resuming = running;
  // Gaps of this paper still mid-ingestion (awaiting the director or validator).
  const unfinishedGaps = linkedGaps.filter((gp) => gp.stage === "pending" || gp.stage === "to_validate");
  const resumingGaps = liveRun?.kind === "resume" && running;

  return (
    <div className="view view--detail">
      <ViewHeader
        eyebrow={`${paper.venue}${paper.year ? ` · ${paper.year}` : ""}`}
        title={paper.title}
        subtitle={paper.authors}
        actions={
          <>
            {paper.sourceUrl && (
              <a className="btn btn--ghost" href={paper.sourceUrl} target="_blank" rel="noreferrer">
                <Icon name="external" /> Open source
              </a>
            )}
          </>
        }
        breadcrumb={[
          { label: "Research" },
          { label: "Papers", onClick: () => setNav({ view: "papers", id: null }) },
          { label: paper.short, current: true },
        ]}
      />

      {/* Live run → the full detailed process (same stage list as the ingest
          transcript) streams right here; the thin summary strip takes over once
          nothing is running. */}
      {liveRun ? <RunTranscript run={liveRun} /> : (
        <div className="pipeline-strip">
          <PaperPipeline stages={paper.stages} />
        </div>
      )}

      {/* Resolved but no downloadable full text → let the user supply a pdf/tex
          source, which uploads and then resumes the pipeline. (Hidden while the
          live transcript is already offering its own drop zone.) */}
      {paper.needsSource && !resuming && liveRun?.status !== "needs_source" && (
        <div className="notice notice--warn">
          <Icon name="alert" />
          <div style={{ flex: 1 }}>
            <b>Full text unavailable</b> — this paper resolved but its PDF/LaTeX source
            couldn't be downloaded (restricted or paywalled). Drop a source below to finish ingestion.
            <SourceDropZone
              busy={uploading}
              onFile={(file) => {
                setUploading(true);
                uploadSource(paper.id, file, paper.resumeQuery).catch(() => setUploading(false));
              }}
            />
            {paper.warnings.length > 0 && (
              <>
                <button className="notice-toggle" onClick={() => setShowWarn((v) => !v)}>
                  <b>{paper.warnings.length} ingestion warning{paper.warnings.length === 1 ? "" : "s"}</b>
                  <Icon name={showWarn ? "chevron-down" : "chevron-right"} size={13} />
                </button>
                {showWarn && <ul>{paper.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>}
              </>
            )}
          </div>
        </div>
      )}

      {/* Incomplete ingestion → offer to resume the agent from where it stalled.
          Hidden while a run for this paper is live — the transcript above already
          shows the progress, so the redundant "resuming…" notice just adds noise. */}
      {!paper.complete && !paper.needsSource && !running && (
        <div className="notice notice--resume">
          <Icon name="clock" />
          <div style={{ flex: 1 }}>
            <b>Ingestion incomplete</b> — {doneStages} of {paper.stages.length} stages done.
            {resuming
              ? " Resuming the agent…"
              : " Resume the agent to finish the remaining stages."}
          </div>
          <button
            className="btn btn--primary"
            onClick={() => startPaperRun(paper.resumeQuery)}
            disabled={resuming}
          >
            <Icon name={resuming ? "clock" : "play"} /> {resuming ? "Resuming…" : "Resume"}
          </button>
        </div>
      )}

      {/* Gaps mined but not yet pushed through the director/validator — resume the
          ingestion tail for just this paper's unfinished gaps. */}
      {unfinishedGaps.length > 0 && !running && (
        <div className="notice notice--resume">
          <Icon name="clock" />
          <div style={{ flex: 1 }}>
            <b>{unfinishedGaps.length} gap{unfinishedGaps.length === 1 ? "" : "s"} awaiting ingestion</b> —
            {resumingGaps ? " deduplicating & validating…" : " run the director & validator to finish them."}
          </div>
          <button className="btn btn--primary" onClick={() => startPaperGapsResume(paper.id)} disabled={resumingGaps}>
            <Icon name={resumingGaps ? "clock" : "play"} /> {resumingGaps ? "Resuming…" : "Resume gaps"}
          </button>
        </div>
      )}

      {paper.warnings.length > 0 && !running && !(paper.needsSource && !resuming) && (
        <div className="notice notice--warn">
          <Icon name="alert" />
          <div style={{ flex: 1 }}>
            <button className="notice-toggle" onClick={() => setShowWarn((v) => !v)}>
              <b>{paper.warnings.length} ingestion warning{paper.warnings.length === 1 ? "" : "s"}</b>
              <Icon name={showWarn ? "chevron-down" : "chevron-right"} size={13} />
            </button>
            {showWarn && <ul>{paper.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>}
          </div>
        </div>
      )}

      <div className="tabbar">
        <TabButton active={tab === "paper"} onClick={() => setTab("paper")} icon="file-text" label="Paper" />
        <TabButton active={tab === "survey"} onClick={() => setTab("survey")} icon="book"
          label={paper.hasSummary ? "Survey" : "Survey —"} disabled={!paper.hasSummary} />
        <TabButton active={tab === "gaps"} onClick={() => setTab("gaps")} icon="search"
          label={linkedGaps.length ? `Gaps · ${linkedGaps.length}` : "Gaps —"}
          disabled={linkedGaps.length === 0} />
      </div>

      {tab === "paper" && (
        <div className="view-grid view-grid--paper">
          <div className="card card--wide">
            <div className="card-label">Abstract</div>
            <p className="card-body">{paper.abstract || "No abstract available."}</p>
          </div>
          {paper.keyFindings && paper.keyFindings.length > 0 && (
            <div className="card card--wide">
              <div className="card-label">Key contributions</div>
              <ul className="finding-list">
                {paper.keyFindings.map((f, i) => (
                  <li key={i}><span className="finding-num">{i + 1}</span><span>{f}</span></li>
                ))}
              </ul>
            </div>
          )}
          <div className="card">
            <div className="card-label">Metadata</div>
            <div className="kv-grid">
              <div className="kv"><span>Authors</span><b>{paper.authors}</b></div>
              <div className="kv"><span>Venue</span><b>{paper.venue}</b></div>
              <div className="kv"><span>Year</span><b>{paper.year ?? "—"}</b></div>
              <div className="kv"><span>Status</span><b>{paper.status}</b></div>
              <div className="kv"><span>Gaps</span><b>{paper.gapCount}</b></div>
              <div className="kv"><span>Added</span><b>{paper.addedAt || "—"}</b></div>
            </div>
          </div>
          <div className="card">
            <div className="card-label">Tags</div>
            <div className="tag-row">
              {paper.tags.length ? paper.tags.map((t) => <span key={t} className="tag">{t}</span>)
                : <span className="empty-state" style={{ padding: 0 }}>No tags</span>}
            </div>
          </div>

        </div>
      )}

      {tab === "gaps" && (
        <div className="view-grid view-grid--paper">
          {linkedGaps.length > 0 ? (
            <div className="card card--wide">
              <div className="card-label">Research gaps from this paper · {linkedGaps.length}</div>
              <div className="link-list">
                {linkedGaps.map((g) => {
                  const badge = gapIngestionBadge(g);
                  return (
                    <button key={g.id} className="link-row link-row--stacked" onClick={() => setNav({ view: "gap", id: g.id })}>
                      <div className="link-row-head">
                        <span className={"gap-severity gap-severity--" + g.severity}>{g.type.replace(/_/g, " ")}</span>
                        <span>{g.title}</span>
                        {badge && <span className={"ingest-badge ingest-badge--" + badge.tone}>{badge.label}</span>}
                        <Icon name="arrow-up-right" className="link-row-go" />
                      </div>
                      {g.short && <p className="link-row-sub">{g.short}</p>}
                      {g.ingestion?.outcome === "duplicate" && g.duplicateOf && (
                        <p className="link-row-sub">Merged into <code>{g.duplicateOf}</code></p>
                      )}
                    </button>
                  );
                })}
              </div>
            </div>
          ) : (
            <div className="empty-state">No research gaps mined from this paper yet.</div>
          )}
        </div>
      )}

      {tab === "survey" && survey && (
        <div className="view-grid view-grid--paper">
          <div className="card card--wide card--ai">
            <div className="card-head-row">
              <div className="card-label"><Icon name="sparkle" /> AI Survey</div>
              {survey.paperType && <span className="ai-badge">{survey.paperType}</span>}
            </div>
            <p className="card-body">{survey.summary}</p>
          </div>

          {paper.verification && (
            <div className="card card--wide">
              <div className="card-head-row">
                <div className="card-label"><Icon name="shield" /> Summary verification</div>
                <span className={"verify-badge verify-badge--" + (paper.verification.status === "ok" ? "ok" : "issues")}>
                  {paper.verification.status === "ok" ? "verified clean" : "issues flagged"}
                </span>
              </div>
              <div className="kv-grid">
                <div className="kv"><span>Attempts</span><b>{paper.verification.attempts ?? "—"}</b></div>
                <div className="kv"><span>Regenerated</span><b>{paper.verification.regenerated ? "yes" : "no"}</b></div>
                <div className="kv"><span>Claims grounded</span><b>{paper.verification.groundedClaims}</b></div>
                <div className="kv"><span>Open issues</span><b>{paper.verification.issueCount}</b></div>
              </div>
              {paper.verification.unmatchedClaims.length > 0 && (
                <div style={{ marginTop: 10 }}>
                  <div className="card-sublabel">Claims not grounded in the source</div>
                  <ul className="bullet-list">
                    {paper.verification.unmatchedClaims.map((c, i) => (
                      <li key={i}><span className="bullet bullet--amber"></span>{c}</li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}

          {(survey.problem || survey.motivation || (survey.priorLimitations?.length ?? 0) > 0) && (
            <div className="card card--wide">
              <div className="card-label">Problem addressed</div>
              {survey.problem && <p className="card-body">{survey.problem}</p>}
              {survey.motivation && (
                <>
                  <div className="card-sublabel" style={{ marginTop: 12 }}>Motivation</div>
                  <p className="card-body">{survey.motivation}</p>
                </>
              )}
              {survey.priorLimitations && survey.priorLimitations.length > 0 && (
                <>
                  <div className="card-sublabel" style={{ marginTop: 12 }}>Limitations of prior work</div>
                  <ul className="bullet-list">
                    {survey.priorLimitations.map((p, i) => (
                      <li key={i}><span className="bullet bullet--amber"></span>{p}</li>
                    ))}
                  </ul>
                </>
              )}
            </div>
          )}
          {survey.strengths.length > 0 && (
            <div className="card">
              <div className="card-label">Key ideas</div>
              <ul className="bullet-list">
                {survey.strengths.map((s, i) => <li key={i}><span className="bullet bullet--green"></span>{s}</li>)}
              </ul>
            </div>
          )}
          {survey.weaknesses.length > 0 && (
            <div className="card">
              <div className="card-label">Stated limitations &amp; open threads</div>
              <ul className="bullet-list">
                {survey.weaknesses.map((s, i) => <li key={i}><span className="bullet bullet--amber"></span>{s}</li>)}
              </ul>
            </div>
          )}
          {survey.metrics && survey.metrics.length > 0 && (
            <div className="card card--wide">
              <div className="card-label">Reported results</div>
              {survey.resultsHeadline && <p className="card-body" style={{ marginBottom: 12 }}>{survey.resultsHeadline}</p>}
              <div className="metric-grid">
                {survey.metrics.map((m, i) => (
                  <div key={i} className="metric">
                    <div className="metric-val">{m.value || "—"}</div>
                    <div className="metric-name">{m.name}</div>
                    {m.dataset && <div className="metric-ds">{m.dataset}</div>}
                    {m.comparison && <div className="metric-cmp">vs {m.comparison}</div>}
                  </div>
                ))}
              </div>
              {survey.resultsCaveats && survey.resultsCaveats.length > 0 && (
                <div style={{ marginTop: 12 }}>
                  <div className="card-sublabel">Caveats</div>
                  <ul className="bullet-list">
                    {survey.resultsCaveats.map((c, i) => (
                      <li key={i}><span className="bullet bullet--amber"></span>{c}</li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}
          {survey.datasets && survey.datasets.length > 0 && (
            <div className="card">
              <div className="card-label">Datasets</div>
              <div className="tag-row">
                {survey.datasets.map((d) => <span key={d} className="tag">{d}</span>)}
              </div>
            </div>
          )}
          {survey.keywords && survey.keywords.length > 0 && (
            <div className="card">
              <div className="card-label">Keywords</div>
              <div className="tag-row">
                {survey.keywords.map((k) => <span key={k} className="tag">{k}</span>)}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
