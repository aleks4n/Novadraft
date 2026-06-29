import { Icon } from "../Icon";
import { ViewHeader } from "../chrome";
import { useWorkspace } from "../../lib/store";
import type { Nav } from "../../lib/types";

export function TopicOverview() {
  const { active, bundle, setNav } = useWorkspace();
  if (!active) return <div className="empty-state">Loading topic…</div>;
  const papers = bundle?.papers ?? [];
  const gaps = bundle?.gaps ?? [];
  const ideas = bundle?.ideas ?? [];

  return (
    <div className="view view--topic">
      <ViewHeader
        eyebrow={active.category}
        title={active.title}
        actions={
          <>
            <button className="btn btn--ghost"><Icon name="star" /> Starred</button>
            <button className="btn btn--ghost"><Icon name="share" /> Share</button>
            <button className="btn btn--primary"><Icon name="edit" /> Edit topic</button>
          </>
        }
        breadcrumb={[{ label: "Research" }, { label: active.shortTitle, current: true }]}
      />

      <div className="view-grid view-grid--topic">
        <div className="card card--wide">
          <div className="card-label">Description</div>
          <p className="card-body">{active.description || "No description yet."}</p>
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

      <div className="section-divider"><span>Workspace</span></div>

      <div className="view-grid view-grid--summary">
        <SummaryCard icon="file-text" label="Reference Papers" count={papers.length} tone="indigo"
          rows={papers.slice(0, 3).map((p) => p.short)} onClick={() => setNav({ view: "papers", id: null })} />
        <SummaryCard icon="compass" label="Research Gaps" count={gaps.length} tone="amber"
          rows={gaps.slice(0, 3).map((g) => g.short)} onClick={() => setNav({ view: "gaps", id: null })} />
        <SummaryCard icon="bulb" label="Research Ideas" count={ideas.length} tone="violet"
          rows={ideas.length ? ideas.slice(0, 3).map((i) => i.short) : ["Generate from gaps →"]}
          onClick={() => setNav({ view: "ideas", id: null })} />
      </div>
    </div>
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
