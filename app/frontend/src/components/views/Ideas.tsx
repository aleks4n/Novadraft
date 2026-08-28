import { useEffect, useState } from "react";
import { Icon, StatusPip } from "../Icon";
import { TabButton, ViewHeader } from "../chrome";
import { IdeaContent } from "./IdeaContent";
import type { IdeaEdits } from "./IdeaContent";
import { IdeaThinking } from "./IdeaThinking";
import { EnvelopeResult } from "../session/EnvelopeResult";
import { LiveThoughts } from "../LiveThoughts";
import { useWorkspace } from "../../lib/store";
import type { Idea } from "../../lib/types";

export function IdeasOverview() {
  const { active, bundle, setNav, runs } = useWorkspace();
  const ideas = bundle?.ideas ?? [];
  // Runs started from *this* view (a session's runs belong to its transcript).
  // Without this the button would fire a real run whose result went nowhere.
  const quickRuns = runs.filter((r) => r.kind === "idea" && !r.sessionId).slice(-3);

  return (
    <div className="view view--list">
      <ViewHeader
        eyebrow={`Research ideas · ${ideas.length}`}
        title="Research Ideas"
        subtitle="Ideas the agent generated from this topic's stated gaps. Each cites the gaps it attacks and the ideas it descends from — open the graph to see the whole structure."
        actions={
          <>
            <button className="btn btn--ghost" onClick={() => setNav({ view: "idea-graph", id: null })}>
              <Icon name="share" /> Graph view
            </button>
            <button className="btn btn--ghost" onClick={() => setNav({ view: "sessions", id: null })}>
              <Icon name="msg" /> New session
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
          No ideas yet. Open a <b>session</b> to develop one in conversation.
        </div>
      ) : null}

      {quickRuns.length > 0 && (
        <div className="quick-runs">
          {quickRuns.map((r) => (
            <div className="quick-run" key={r.id}>
              <LiveThoughts run={r} />
              {r.envelope && (
                <EnvelopeResult
                  envelope={r.envelope}
                  runId={r.id}
                  baseline={r.envelope.idea.idea_id
                    ? ideas.find((i) => i.id === r.envelope!.idea.idea_id) ?? null : null}
                  onOpenIdea={(id) => setNav({ view: "idea", id })}
                />
              )}
            </div>
          ))}
        </div>
      )}

      {ideas.length > 0 && (
        <div className="ideas-grid">
          {ideas.map((i, idx) => (
            <button key={i.id} className="idea-card" onClick={() => setNav({ view: "idea", id: i.id })}>
              <div className="idea-card-head">
                <div className="idea-card-num">#{String(idx + 1).padStart(2, "0")}</div>
              </div>
              <div className="idea-card-title">{i.title}</div>
              <p className="idea-card-desc">{i.oneLiner || i.description}</p>
              <div className="idea-card-scores">
                <MiniScore value={i.novelty} label="novelty" />
                <MiniScore value={i.feasibility} label="feasibility" />
                <MiniScore value={i.impact} label="impact" />
              </div>
              <div className="idea-card-foot">
                <div className="idea-card-links">
                  {i.addresses.length > 0 && (
                    <span className="mini-link"><Icon name="compass" /> {i.addresses.length} gap{i.addresses.length === 1 ? "" : "s"}</span>
                  )}
                  {i.relations.length > 0 && (
                    <span className="mini-link"><Icon name="share" /> {i.relations.length} relation{i.relations.length === 1 ? "" : "s"}</span>
                  )}
                  {i.runCount > 0 && (
                    <span className="mini-link"><Icon name="flask" /> {i.runCount} run{i.runCount === 1 ? "" : "s"}</span>
                  )}
                </div>
                <span className="idea-card-date">{i.updatedAt}</span>
              </div>
            </button>
          ))}

          <button className="idea-card idea-card--new" onClick={() => setNav({ view: "sessions", id: null })}>
            <div className="idea-card-plus"><Icon name="plus" /></div>
            <div className="idea-card-title">New session</div>
            <p className="idea-card-desc">Open a conversation with the agent and develop a direction of your own.</p>
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

/** One idea with its `runs[]` — the bundle carries the spine only, so the detail
 *  page re-fetches. Falls back to the bundle copy while that is in flight, which
 *  is what keeps the page from flashing empty on every navigation. */
export function useFullIdea(id: string): Idea | undefined {
  const { bundle, loadIdea } = useWorkspace();
  const light = bundle?.ideas.find((x) => x.id === id);
  const [full, setFull] = useState<Idea | null>(null);

  useEffect(() => {
    let live = true;
    setFull(null);
    loadIdea(id).then((i) => { if (live) setFull(i); });
    return () => { live = false; };
  }, [id, loadIdea, bundle?.meta.updatedAt]);

  return full ?? light;
}

export function IdeaDetail({ id }: { id: string }) {
  const { setNav, updateIdea } = useWorkspace();
  const [tab, setTab] = useState<"content" | "thinking">("content");
  const idea = useFullIdea(id);
  if (!idea) return <div className="empty-state">Idea not found.</div>;

  const save = async (edits: IdeaEdits) => { await updateIdea(id, edits); };

  return (
    <div className="view view--idea">
      <ViewHeader
        eyebrow={
          <span>
            {idea.runCount} run{idea.runCount === 1 ? "" : "s"} · updated {idea.updatedAt}
          </span>
        }
        title={idea.title}
        subtitle={idea.oneLiner}
        actions={
          <>
            <button className="btn btn--ghost" onClick={() => setNav({ view: "idea-graph", id: null })}>
              <Icon name="share" /> In graph
            </button>
            <button className="btn btn--ghost"><Icon name="star" /></button>
          </>
        }
        breadcrumb={[
          { label: "Research" },
          { label: "Ideas", onClick: () => setNav({ view: "ideas", id: null }) },
          { label: idea.short, current: true },
        ]}
      />

      <div className="tabbar">
        <TabButton active={tab === "content"} onClick={() => setTab("content")} icon="file-text" label="Content" />
        <TabButton active={tab === "thinking"} onClick={() => setTab("thinking")}
          icon="flask" label={`Thinking${idea.runCount ? ` · ${idea.runCount}` : ""}`} />
      </div>

      {tab === "content"
        ? <IdeaContent idea={idea} editable onSave={save} footer={<IdeaLinks idea={idea} />} />
        : <IdeaThinking idea={idea} />}
    </div>
  );
}

/** What the idea is wired to: the gaps it claims to solve and its typed edges to
 *  other ideas. Every relation carries the one-line rationale that created it —
 *  edges are evidence of reasoning, so the rationale is shown, not hidden. */
function IdeaLinks({ idea }: { idea: Idea }) {
  const { bundle, setNav } = useWorkspace();
  const allGaps = bundle?.allGaps ?? bundle?.gaps ?? [];
  const gaps = idea.addresses.map((gid) => allGaps.find((g) => g.id === gid) ?? null);

  return (
    <div className="idea-links">
      {idea.addresses.length > 0 && (
        <div className="card">
          <div className="card-label">Addresses</div>
          <div className="link-list">
            {idea.addresses.map((gid, i) => (
              <button key={gid} className="link-row" onClick={() => setNav({ view: "gap", id: gid })}>
                <Icon name="compass" />
                <span>{gaps[i]?.short || gid}</span>
                <Icon name="arrow-up-right" className="link-row-go" />
              </button>
            ))}
          </div>
        </div>
      )}

      {idea.relations.length > 0 && (
        <div className="card">
          <div className="card-label">Relations</div>
          <div className="link-list">
            {idea.relations.map((r, i) => (
              <button key={i} className="link-row link-row--tall"
                onClick={() => setNav({ view: "idea", id: r.target })}>
                <span className={"rel-pip rel-pip--" + r.type.replace("-", "")}>{r.type}</span>
                <span className="link-row-stack">
                  <span>{bundle?.ideas.find((x) => x.id === r.target)?.short || r.target}</span>
                  {r.rationale && <span className="link-row-sub">{r.rationale}</span>}
                </span>
                <Icon name="arrow-up-right" className="link-row-go" />
              </button>
            ))}
          </div>
        </div>
      )}

      {idea.provenance.length > 0 && (
        <div className="card card--wide">
          <div className="card-label">Provenance</div>
          <ul className="bullet-list">
            {idea.provenance.map((p, i) => (
              <li key={i}>
                <span className="bullet"></span>
                {p.claim}
                <span className="prov-src">{p.source}{p.via ? ` · via ${p.via}` : ""}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {idea.origin?.runId && (
        <div className="card">
          <div className="card-label">Origin</div>
          <p className="card-body">
            {idea.origin.mode || "run"} · {idea.origin.style || "—"}
            {idea.origin.parentIdeaId && <> · from <b>{idea.origin.parentIdeaId}</b></>}
            {idea.origin.suggested && <> · proposed as <b>{idea.origin.suggested}</b></>}
          </p>
        </div>
      )}

      {/* Which parts of this record were copied rather than independently arrived
          at — the honest caveat on a child or variant's content. */}
      {idea.inheritedFrom?.ideaId && (
        <div className="card">
          <div className="card-label">Inherited</div>
          <p className="card-body">
            Content copied from{" "}
            <button className="inline-link"
              onClick={() => setNav({ view: "idea", id: idea.inheritedFrom!.ideaId! })}>
              {bundle?.ideas.find((x) => x.id === idea.inheritedFrom!.ideaId)?.short
                || idea.inheritedFrom.ideaId}
            </button>
            {idea.inheritedFrom.uptoRunId && <> up to <b>{idea.inheritedFrom.uptoRunId}</b></>}.
            Anything after that point is this idea's own.
          </p>
        </div>
      )}
    </div>
  );
}

export { StatusPip };
