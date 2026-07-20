import { useState } from "react";
import { Icon, ScoreBar, StatusPip } from "../Icon";
import { TabButton, ViewHeader } from "../chrome";
import { useWorkspace } from "../../lib/store";
import type { Idea } from "../../lib/types";

export function IdeasOverview() {
  const { active, bundle, setNav, startIdeaRun, runs } = useWorkspace();
  const ideas = bundle?.ideas ?? [];
  const generating = runs.some((r) => r.kind === "idea" && r.status === "running");

  return (
    <div className="view view--list">
      <ViewHeader
        eyebrow={`Research ideas · ${ideas.length}`}
        title="Research Ideas"
        subtitle="Ideas the agent generated from this topic's stated gaps. Each is scored on novelty, feasibility, and impact, and cites the gaps and papers it builds on."
        actions={
          <>
            <button className="btn btn--ghost" onClick={() => setNav({ view: "new-idea", id: null })}>
              <Icon name="plus" /> New ideation
            </button>
            <button className="btn btn--primary" disabled={generating}
              onClick={() => startIdeaRun({ mode: "generate", count: 3 })}>
              <Icon name="sparkle" /> {generating ? "Generating…" : "Generate from gaps"}
            </button>
          </>
        }
        breadcrumb={[
          { label: "Research" },
          { label: active?.shortTitle || "Topic", onClick: () => setNav({ view: "topic", id: null }) },
          { label: "Ideas", current: true },
        ]}
      />

      {ideas.length === 0 ? (
        <div className="empty-state">
          No ideas yet. Use <b>Generate from gaps</b> to have the agent propose some,
          or <b>New ideation</b> to expand your own.
        </div>
      ) : (
        <div className="ideas-grid">
          {ideas.map((i, idx) => (
            <button key={i.id} className="idea-card" onClick={() => setNav({ view: "idea", id: i.id })}>
              <div className="idea-card-head">
                <div className="idea-card-num">#{String(idx + 1).padStart(2, "0")}</div>
                <StatusPip status={i.status} />
              </div>
              <div className="idea-card-title">{i.title}</div>
              <p className="idea-card-desc">{i.description}</p>
              <div className="idea-card-scores">
                <MiniScore value={i.novelty} label="novelty" />
                <MiniScore value={i.feasibility} label="feasibility" />
                <MiniScore value={i.impact} label="impact" />
              </div>
              <div className="idea-card-foot">
                <div className="idea-card-links">
                  {i.relatedGaps.length > 0 && (
                    <span className="mini-link"><Icon name="compass" /> {i.relatedGaps.length} gap{i.relatedGaps.length === 1 ? "" : "s"}</span>
                  )}
                  <span className="mini-link"><Icon name="file-text" /> {i.relatedPapers.length} papers</span>
                </div>
                <span className="idea-card-date">{i.updatedAt}</span>
              </div>
            </button>
          ))}

          <button className="idea-card idea-card--new" onClick={() => setNav({ view: "new-idea", id: null })}>
            <div className="idea-card-plus"><Icon name="plus" /></div>
            <div className="idea-card-title">New ideation</div>
            <p className="idea-card-desc">Describe a direction in your own words and let the agent develop it.</p>
          </button>
        </div>
      )}
    </div>
  );
}

function MiniScore({ value, label }: { value: number | null; label: string }) {
  return (
    <div className="mini-score">
      <span className="mini-score-num">{value == null ? "—" : Math.round(value * 100)}</span>
      <span className="mini-score-lbl">{label}</span>
    </div>
  );
}

export function IdeaDetail({ id }: { id: string }) {
  const { bundle, setNav } = useWorkspace();
  const [tab, setTab] = useState<"report" | "brainstorm" | "plan" | "paper">("report");
  const i = bundle?.ideas.find((x) => x.id === id);
  if (!i) return <div className="empty-state">Idea not found.</div>;

  return (
    <div className="view view--idea">
      <ViewHeader
        eyebrow={<span><StatusPip status={i.status} /> · {i.source === "user" ? "From your sketch" : "Agent-generated"} · {i.updatedAt}</span>}
        title={i.title}
        actions={
          <>
            <button className="btn btn--ghost"><Icon name="star" /></button>
            <button className="btn btn--ghost"><Icon name="share" /></button>
          </>
        }
        breadcrumb={[
          { label: "Research" },
          { label: "Ideas", onClick: () => setNav({ view: "ideas", id: null }) },
          { label: i.short, current: true },
        ]}
      />

      <div className="tabbar">
        <TabButton active={tab === "report"} onClick={() => setTab("report")} icon="file-text" label="Report" />
        <TabButton active={tab === "brainstorm"} onClick={() => setTab("brainstorm")} icon="pen" label="Brainstorm" />
        <TabButton active={tab === "plan"} onClick={() => setTab("plan")} icon="calendar" label="Plan" />
        <TabButton active={tab === "paper"} onClick={() => setTab("paper")} icon="book" label="Paper" />
      </div>

      {tab === "report" ? <ReportTab idea={i} /> : <RefinementPlaceholder tab={tab} />}
    </div>
  );
}

function ReportTab({ idea: i }: { idea: Idea }) {
  const { bundle, setNav } = useWorkspace();
  const gaps = i.relatedGaps.map((gid) => bundle?.gaps.find((g) => g.id === gid)).filter(Boolean);
  const papers = i.relatedPapers.map((pid) => bundle?.papers.find((p) => p.id === pid)).filter(Boolean);
  const ev = i.evaluation;

  return (
    <div className="view-grid view-grid--paper">
      <div className="card card--wide">
        <div className="card-label">Idea</div>
        <p className="card-body">{i.description}</p>
      </div>

      <div className="card card--wide card--ai">
        <div className="card-head-row">
          <div className="card-label"><Icon name="sparkle" /> Summary</div>
          <span className="ai-badge">{i.source === "user" ? "expanded" : "generated"}</span>
        </div>
        <p className="card-body">{i.summary}</p>
      </div>

      <div className="card">
        <div className="card-label">Evaluation</div>
        {ev.noveltyScore != null && <ScoreBar value={ev.noveltyScore} label="Novelty" color="#5B5BF6" />}
        {ev.rigourScore != null && <ScoreBar value={ev.rigourScore} label="Rigour" color="#5B7BC7" />}
        {ev.feasibilityScore != null && <ScoreBar value={ev.feasibilityScore} label="Feasibility" color="#5B9C75" />}
        {ev.impactScore != null && <ScoreBar value={ev.impactScore} label="Impact" color="#C75B5B" />}
      </div>

      {ev.risks.length > 0 && (
        <div className="card">
          <div className="card-label">Risks</div>
          <ul className="bullet-list">
            {ev.risks.map((r, idx) => <li key={idx}><span className="bullet bullet--amber"></span>{r}</li>)}
          </ul>
        </div>
      )}

      {i.method.length > 0 && (
        <div className="card card--wide">
          <div className="card-label">Method</div>
          <ol className="goal-list">
            {i.method.map((m, idx) => <li key={idx}><span className="goal-num">{idx + 1}</span>{m}</li>)}
          </ol>
        </div>
      )}

      {gaps.length > 0 && (
        <div className="card">
          <div className="card-label">Builds on gaps</div>
          <div className="link-list">
            {gaps.map((g) => (
              <button key={g!.id} className="link-row" onClick={() => setNav({ view: "gap", id: g!.id })}>
                <Icon name="compass" /><span>{g!.short}</span>
                <Icon name="arrow-up-right" className="link-row-go" />
              </button>
            ))}
          </div>
        </div>
      )}

      {papers.length > 0 && (
        <div className="card">
          <div className="card-label">Grounded in papers</div>
          <div className="link-list">
            {papers.map((p) => (
              <button key={p!.id} className="link-row" onClick={() => setNav({ view: "paper", id: p!.id })}>
                <Icon name="file-text" /><span>{p!.short}</span>
                <span className="link-row-meta">{p!.year ?? ""}</span>
                <Icon name="arrow-up-right" className="link-row-go" />
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function RefinementPlaceholder({ tab }: { tab: string }) {
  const labels: Record<string, string> = {
    brainstorm: "Brainstorm whiteboard",
    plan: "Implementation plan",
    paper: "Paper template",
  };
  return (
    <div className="view-grid view-grid--paper">
      <div className="card card--wide">
        <div className="card-label">{labels[tab]}</div>
        <p className="card-body">
          This is part of Phase 3 (idea refinement) — the Discussion, Evaluation, and Synthesis
          agents that turn a report into a worked plan and paper draft. The report on the left is
          live; this surface comes next.
        </p>
      </div>
    </div>
  );
}
