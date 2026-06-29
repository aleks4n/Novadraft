import { useEffect, useRef, useState } from "react";
import { Icon } from "../Icon";
import { TabButton, ViewHeader } from "../chrome";
import { PaperPipeline } from "../PaperPipeline";
import { RunTranscript } from "../RunTranscript";
import { SourceDropZone } from "../SourceDropZone";
import { useWorkspace } from "../../lib/store";
import { api } from "../../lib/api";
import type { Paper } from "../../lib/types";
import { SUGGESTIONS } from "./AddPaper";

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

export function PapersOverview() {
  const { active, bundle, setNav, run, startPaperRun } = useWorkspace();
  const papers = bundle?.papers ?? [];
  const [query, setQuery] = useState("");
  const [tryOpen, setTryOpen] = useState(false);
  const tryRef = useRef<HTMLDivElement>(null);
  const running = run.kind === "paper" && run.status === "running";

  useEffect(() => {
    if (!tryOpen) return;
    const onDoc = (e: MouseEvent) => {
      if (tryRef.current && !tryRef.current.contains(e.target as Node)) setTryOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [tryOpen]);

  const submit = () => {
    if (!query.trim() || running) return;
    startPaperRun(query);
  };

  return (
    <div className="view view--list">
      <ViewHeader
        eyebrow={`Reference papers · ${papers.length}`}
        title="Reference Papers"
        subtitle="Papers ingested into this knowledge base. Each is fetched, parsed, summarized & verified, then mined for the research gaps it states — the bar shows how far that pipeline got."
        actions={
          <button className="btn btn--ghost"><Icon name="filter" /> Filter</button>
        }
        breadcrumb={[
          { label: "Research" },
          { label: active?.shortTitle || "Topic", onClick: () => setNav({ view: "topic", id: null }) },
          { label: "Papers", current: true },
        ]}
      />

      {/* Inline ingestion: enter an arXiv ID / URL / DOI / title, or pick a sample
          from the dropdown. Mirrors the Add-paper view but as a one-line bar. */}
      <div className="papers-ingest">
        <div className="papers-ingest-bar">
          <Icon name="plus" className="papers-ingest-icon" />
          <input
            className="papers-ingest-input"
            placeholder="arXiv ID (1706.03762), URL, DOI, or paper title…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") submit(); }}
            disabled={running}
          />
          <div className="papers-ingest-try" ref={tryRef}>
            <button
              className={"btn btn--ghost" + (tryOpen ? " btn--active" : "")}
              onClick={() => setTryOpen((v) => !v)}
              disabled={running}
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
                      onClick={() => { setQuery(s.q); setTryOpen(false); }}
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
          <button className="btn btn--primary" onClick={submit} disabled={running || !query.trim()}>
            <Icon name={running ? "clock" : "arrow-right"} /> Ingest
          </button>
        </div>
        {run.kind === "paper" && run.status !== "idle" && <RunTranscript run={run} />}
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
  const { bundle, activeSlug, setNav, run, startPaperRun, uploadSource } = useWorkspace();
  const [paper, setPaper] = useState<Paper | null>(null);
  const [tab, setTab] = useState<"paper" | "survey" | "gaps">("paper");
  const [showWarn, setShowWarn] = useState(false);
  const [uploading, setUploading] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setPaper(null);
    if (activeSlug) api.paper(activeSlug, id).then((p) => { if (!cancelled) setPaper(p); }).catch(() => {});
    return () => { cancelled = true; };
  }, [activeSlug, id]);

  // When a resume run for this paper finishes, re-fetch so the pipeline strip and
  // any newly-extracted survey/gaps reflect the progress the agent just made.
  useEffect(() => {
    if (run.kind === "paper" && run.status === "done" && activeSlug)
      api.paper(activeSlug, id).then(setPaper).catch(() => {});
  }, [run.status, run.kind, activeSlug, id]);

  if (!paper) return <div className="empty-state">Loading paper…</div>;
  const survey = paper.survey;
  const linkedGaps = (bundle?.gaps ?? []).filter((g) => g.derivedFrom.includes(paper.id));
  const doneStages = paper.stages.filter((s) => s.done).length;
  // This paper's resume run is the live one when its query matches.
  const runForThisPaper =
    run.kind === "paper" && run.status !== "idle" && run.query === paper.resumeQuery;
  const resuming = runForThisPaper && run.status === "running";

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
            <button className="btn btn--ghost"><Icon name="copy" /> Cite</button>
          </>
        }
        breadcrumb={[
          { label: "Research" },
          { label: "Papers", onClick: () => setNav({ view: "papers", id: null }) },
          { label: paper.short, current: true },
        ]}
      />

      {/* Where this paper got to in the ingestion pipeline. */}
      <div className="pipeline-strip">
        <PaperPipeline stages={paper.stages} />
      </div>

      {/* Resolved but no downloadable full text → let the user supply a pdf/tex
          source, which uploads and then resumes the pipeline. */}
      {paper.needsSource && !resuming && (
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

      {/* Incomplete ingestion → offer to resume the agent from where it stalled,
          streaming the same process view the Add-paper step shows. */}
      {!paper.complete && !paper.needsSource && (
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

      {runForThisPaper && <RunTranscript run={run} />}

      {paper.warnings.length > 0 && !(paper.needsSource && !resuming) && (
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
                {linkedGaps.map((g) => (
                  <button key={g.id} className="link-row link-row--stacked" onClick={() => setNav({ view: "gap", id: g.id })}>
                    <div className="link-row-head">
                      <span className={"gap-severity gap-severity--" + g.severity}>{g.type.replace(/_/g, " ")}</span>
                      <span>{g.title}</span>
                      <Icon name="arrow-up-right" className="link-row-go" />
                    </div>
                    {g.short && <p className="link-row-sub">{g.short}</p>}
                  </button>
                ))}
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
