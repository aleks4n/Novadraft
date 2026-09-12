// The **Thinking** tab — the decision-graph surface from the vault's *UI Surfaces*
// note: "a separate panel showing the *whole* accumulated graph for one idea:
// every run appended end to end, each under its own header (mode, N, model,
// styles, context)."
//
// This is the reasoning graph, and it is deliberately kept apart from the idea
// graph: `runs[]` is append-only evidence of how the idea came to be, and nothing
// on this tab writes. Reading aids the note asks for and this implements: node
// colour by status, a style badge per node, run boundaries as bands, a `show
// pruned` toggle, and a visible truncation marker so a partial run can never look
// like a finished one.
import { useState } from "react";
import { Icon } from "../Icon";
import { NodeInspector, ThoughtTree, TruncationNote } from "../ThoughtTree";
import type { Idea, IdeaRun, ThoughtNode } from "../../lib/types";

export function IdeaThinking({ idea }: { idea: Idea }) {
  const [showPruned, setShowPruned] = useState(true);
  const [selected, setSelected] = useState<ThoughtNode | null>(null);

  if (!idea.runs.length) {
    // `runCount` comes from the spine, `runs` from the full fetch — so the two
    // disagreeing means the trace failed to load, not that there is none. Saying
    // "no thinking recorded" there would be a lie about the record.
    if (idea.runCount > 0) {
      return (
        <div className="view-grid view-grid--paper">
          <div className="card card--wide">
            <div className="card-label">Trace not loaded</div>
            <p className="card-body">
              This idea has {idea.runCount} recorded run{idea.runCount === 1 ? "" : "s"}, but the
              trace could not be read. Reload the page; if it persists, the run entry on disk is
              malformed — the backend log names the field.
            </p>
          </div>
        </div>
      );
    }
    return (
      <div className="view-grid view-grid--paper">
        <div className="card card--wide">
          <div className="card-label">No thinking recorded yet</div>
          <p className="card-body">
            Every creative-thinker run appends one entry to this idea's <code>runs[]</code>,
            and the entries concatenate into one continuous graph. Work on this idea in a
            session and its first run lands here.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="thinking">
      <div className="thinking-bar">
        <span className="thinking-count">
          {idea.runs.length} run{idea.runs.length === 1 ? "" : "s"} ·{" "}
          {idea.runs.reduce((n, r) => n + r.nodes.length, 0)} thoughts
        </span>
        <label className="thinking-toggle">
          <input type="checkbox" checked={showPruned} onChange={(e) => setShowPruned(e.target.checked)} />
          show pruned
        </label>
      </div>

      <div className="thinking-body">
        <div className="thinking-runs">
          {idea.runs.map((run) => (
            <RunBand key={run.runId} run={run} showPruned={showPruned}
              selected={selected} onSelect={setSelected} />
          ))}
        </div>
        {selected && <NodeInspector node={selected} onClose={() => setSelected(null)} />}
      </div>
    </div>
  );
}

function RunBand({
  run, showPruned, selected, onSelect,
}: {
  run: IdeaRun; showPruned: boolean;
  selected: ThoughtNode | null; onSelect: (n: ThoughtNode) => void;
}) {
  const [open, setOpen] = useState(true);
  const s = run.outcome;

  return (
    <div className={"run-band run-band--" + s.status}>
      <button className="run-band-head" onClick={() => setOpen(!open)}>
        <Icon name={open ? "chevron-down" : "chevron-right"} />
        <span className="run-band-mode">{run.mode}</span>
        <span className="run-band-shape">width {run.width ?? "—"} · depth {run.depth ?? "—"}</span>
        {run.styles.length > 0 && (
          <span className="run-band-styles">{run.styles.join(" · ")}</span>
        )}
        <span className={"run-band-status run-band-status--" + s.status}>{s.status}</span>
        <span className="run-band-time">{(run.timestamp || "").slice(0, 16).replace("T", " ")}</span>
      </button>

      {open && (
        <div className="run-band-body">
          {run.task && (
            <div className="run-band-task"><b>Task</b> {run.task}</div>
          )}
          {s.answer && <div className="run-band-answer"><b>Answer</b> {s.answer}</div>}

          <ThoughtTree run={run} showPruned={showPruned} selected={selected} onSelect={onSelect} />

          {(s.summary.accepted || s.summary.rejected || s.summary.open.length > 0) && (
            <div className="run-band-summary">
              {s.summary.accepted && <p><b>Settled</b> {s.summary.accepted}</p>}
              {s.summary.rejected && <p><b>Dropped</b> {s.summary.rejected}</p>}
              {s.summary.open.length > 0 && (
                <p><b>Left open</b> {s.summary.open.join(" · ")}</p>
              )}
            </div>
          )}

          {/* Truncation must be visible — a run stopped by a ceiling is not a
              finished exploration, and nothing else on the page says so. */}
          <TruncationNote run={run} />
          {s.escalation && (
            <div className="run-band-escalation">
              <b>Escalated</b> {s.escalation}
              {s.escalationDiagnosis && ` — ${s.escalationDiagnosis}`}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
