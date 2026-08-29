import { useEffect, useState } from "react";
import { Icon } from "../Icon";
import { ViewHeader } from "../chrome";
import { useWorkspace } from "../../lib/store";
import type { Nav, Paper, SeminalPaper } from "../../lib/types";

// Loose title match so we can tell whether a seminal paper is already ingested —
// exact strings rarely line up (subtitles, casing, punctuation), so compare on a
// normalized alphanumeric key and accept either being a prefix of the other.
function normTitle(t: string): string {
  return t.toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
}
function findIngested(title: string, papers: Paper[]): Paper | undefined {
  const key = normTitle(title);
  if (!key) return undefined;
  return papers.find((p) => {
    const pk = normTitle(p.title);
    return pk === key || pk.startsWith(key) || key.startsWith(pk);
  });
}

export function TopicOverview() {
  const { active, bundle, setNav, runs, startPaperRun, updateTopic } = useWorkspace();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(active?.description ?? "");
  useEffect(() => { if (!editing) setDraft(active?.description ?? ""); }, [active?.description, editing]);

  // Title edits are a plain hand override — saved as-is, with no LLM spelling
  // correction (that only runs once, when the category agent creates the topic).
  const [titleEditing, setTitleEditing] = useState(false);
  const [titleDraft, setTitleDraft] = useState(active?.title ?? "");
  useEffect(() => { if (!titleEditing) setTitleDraft(active?.title ?? ""); }, [active?.title, titleEditing]);

  if (!active) return <div className="empty-state">Loading topic…</div>;
  const papers = bundle?.papers ?? [];
  // Live gaps across all stages (minus archived duplicates) — not just pending,
  // which empties out as gaps flow through the pipeline.
  const gaps = (bundle?.allGaps ?? bundle?.gaps ?? []).filter((g) => (g.stage ?? g.ingestion?.stage) !== "archive");
  const ideas = bundle?.ideas ?? [];
  const seminal = active.seminalPapers ?? [];

  const commitDescription = async () => {
    setEditing(false);
    if (draft !== active.description) await updateTopic({ description: draft });
  };

  const commitTitle = async () => {
    setTitleEditing(false);
    const trimmed = titleDraft.trim();
    if (trimmed && trimmed !== active.title) await updateTopic({ name: trimmed });
  };

  return (
    <div className="view view--topic">
      <ViewHeader
        eyebrow={active.category}
        title={
          titleEditing ? (
            <input
              className="view-title-input" autoFocus value={titleDraft}
              onChange={(e) => setTitleDraft(e.target.value)}
              onBlur={commitTitle}
              onKeyDown={(e) => {
                if (e.key === "Escape") { setTitleDraft(active.title); setTitleEditing(false); }
                if (e.key === "Enter") commitTitle();
              }}
            />
          ) : (
            active.title
          )
        }
        actions={
          <>
            <button className="btn btn--ghost"><Icon name="star" /> Starred</button>
            <button className="btn btn--ghost"><Icon name="share" /> Share</button>
            <button className="btn btn--primary" onClick={() => setTitleEditing(true)}>
              <Icon name="edit" /> Edit topic
            </button>
          </>
        }
        breadcrumb={[{ label: "Research" }, { label: active.shortTitle, current: true }]}
      />

      <div className="view-grid view-grid--topic">
        <div className="card card--wide">
          <div className="card-head-row">
            <div className="card-label">Description</div>
            {!editing && (
              <button className="ghost-btn idea-edit-btn" onClick={() => setEditing(true)} title="Edit description">
                <Icon name="edit" />
              </button>
            )}
          </div>
          {editing ? (
            <>
              <textarea
                className="idea-textarea" autoFocus rows={4} value={draft}
                placeholder="No description yet."
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Escape") { setDraft(active.description); setEditing(false); }
                  if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) commitDescription();
                }}
              />
              <div className="idea-edit-actions">
                <button className="btn btn--ghost" onClick={() => { setDraft(active.description); setEditing(false); }}>Cancel</button>
                <button className="btn btn--primary" onClick={commitDescription}>Save</button>
              </div>
            </>
          ) : (
            <p className={"card-body" + (active.description ? "" : " card-body--empty")}>
              {active.description || "No description yet."}
            </p>
          )}
        </div>

        <div className="card">
          <div className="card-label">Topic stats</div>
          <div className="kv-grid">
            <div className="kv"><span>Created</span><b>{active.createdAt || "—"}</b></div>
            <div className="kv"><span>Updated</span><b>{active.updatedAt || "—"}</b></div>
            <div className="kv"><span>Papers</span><b>{papers.length}</b></div>
            <div className="kv"><span>Status</span><b><span className="status-dot status-dot--active"></span> Active</b></div>
          </div>
        </div>

        {active.goals.length > 0 && (
          <div className="card">
            <div className="card-label">Research goals</div>
            <ol className="goal-list">
              {active.goals.map((g, i) => <li key={i}><span className="goal-num">{i + 1}</span>{g}</li>)}
            </ol>
          </div>
        )}

        {active.keywords.length > 0 && (
          <div className="card card--wide">
            <div className="card-label">Keywords</div>
            <div className="tag-row">
              {active.keywords.map((k, i) => <span key={i} className="tag">{k}</span>)}
            </div>
          </div>
        )}
      </div>

      {seminal.length > 0 && (
        <>
          <div className="section-divider"><span>Foundational Papers</span></div>
          <p className="section-lead">
            Seminal papers of this field, suggested when the topic was created. Click one to
            ingest it through the full pipeline — fetch, parse, summarize, and mine its gaps.
          </p>
          <div className="foundational-grid">
            {seminal.map((sp, i) => (
              <FoundationalCard
                key={i}
                paper={sp}
                ingested={findIngested(sp.title, papers)}
                running={runs.some(
                  (r) => r.kind === "paper" && r.query === sp.title && r.status === "running",
                )}
                onOpen={(id) => setNav({ view: "paper", id })}
                onIngest={() => startPaperRun(sp.title, { autoOpen: true })}
              />
            ))}
          </div>
        </>
      )}

      <div className="section-divider"><span>Workspace</span></div>

      <div className="view-grid view-grid--summary">
        <SummaryCard icon="file-text" label="Reference Papers" count={papers.length} tone="indigo"
          rows={papers.slice(0, 3).map((p) => p.short)} onClick={() => setNav({ view: "papers", id: null })} />
        <SummaryCard icon="compass" label="Research Gaps" count={gaps.length} tone="amber"
          rows={gaps.slice(0, 3).map((g) => g.short)} onClick={() => setNav({ view: "gaps", id: null })} />
        <SummaryCard icon="bulb" label="Research Ideas" count={ideas.length} tone="violet"
          rows={ideas.length ? ideas.slice(0, 3).map((i) => i.short) : ["Start a session →"]}
          onClick={() => setNav({ view: "ideas", id: null })} />
      </div>
    </div>
  );
}

function FoundationalCard({
  paper, ingested, running, onOpen, onIngest,
}: {
  paper: SeminalPaper;
  ingested?: Paper;
  running: boolean;
  onOpen: (id: string) => void;
  onIngest: () => void;
}) {
  // Three states: already in the library (open it), currently ingesting (spinner,
  // disabled), or not yet added (click to push it through the pipeline).
  const state = ingested ? "done" : running ? "running" : "idle";
  const onClick = () => {
    if (ingested) onOpen(ingested.id);
    else if (!running) onIngest();
  };
  return (
    <button className={"foundational-card foundational-card--" + state} onClick={onClick}>
      <div className="foundational-head">
        <div className="foundational-icon"><Icon name="file-text" /></div>
        <div className="foundational-status">
          {state === "done" && <span className="foundational-badge foundational-badge--done"><Icon name="check" /> In library</span>}
          {state === "running" && <span className="foundational-badge foundational-badge--running"><span className="sidebar-spin" /> Ingesting…</span>}
          {state === "idle" && <span className="foundational-badge foundational-badge--idle"><Icon name="plus" /> Ingest</span>}
        </div>
      </div>
      <div className="foundational-title">{paper.title}</div>
      <div className="foundational-meta">
        {paper.authors || "Unknown authors"}{paper.year ? ` · ${paper.year}` : ""}
      </div>
      {paper.why && <div className="foundational-why">{paper.why}</div>}
      <div className="foundational-cta">
        {state === "done" ? <>Open paper <Icon name="arrow-right" /></>
          : state === "running" ? <>Running through pipeline…</>
          : <>Add to knowledge base <Icon name="arrow-right" /></>}
      </div>
    </button>
  );
}

function SummaryCard({
  icon, label, count, onClick, tone, rows,
}: {
  icon: string; label: string; count: number; onClick: () => void; tone: string; rows: string[];
}) {
  return (
    <button className={"summary-card summary-card--" + tone} onClick={onClick}>
      <div className="summary-head">
        <div className="summary-icon"><Icon name={icon} /></div>
        <div className="summary-label">{label}</div>
        <div className="summary-count">{count}</div>
      </div>
      <ul className="summary-rows">{rows.map((r, i) => <li key={i}>{r}</li>)}</ul>
      <div className="summary-cta">Open <Icon name="arrow-right" /></div>
    </button>
  );
}

export type { Nav };
