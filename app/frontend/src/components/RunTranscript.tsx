// Live view of an agent run, shown inline in the Add-paper / New-idea step.
// The agent is part of that workflow, not a separate assistant, so we show the
// *process* as a small stage pipeline (fetch → extract → summarize → gaps →
// save) rather than a wall of internal nodes. The raw event log is still there,
// one click away, for when you want the detail.
import { useEffect, useRef, useState } from "react";
import { Icon } from "./Icon";
import { SourceDropZone } from "./SourceDropZone";
import { useWorkspace } from "../lib/store";
import type { RunEvent, RunState } from "../lib/types";

interface Stage { key: string; label: string; hint?: string }

const PAPER_STAGES: Stage[] = [
  { key: "fetch", label: "Fetch paper", hint: "Resolve & download the source" },
  { key: "extract", label: "Extract sections", hint: "Parse the full text" },
  { key: "summarize", label: "Summarize & verify", hint: "Draft and self-check the summary" },
  { key: "gap", label: "Mine research gaps", hint: "Find the gaps the paper states" },
  { key: "assemble", label: "Save to knowledge base" },
];

const IDEA_STAGES: Stage[] = [
  { key: "enter", label: "Load knowledge base", hint: "Read gaps & papers" },
  { key: "generate", label: "Generate ideas" },
  { key: "store", label: "Save ideas" },
];

// Stages whose subgraph runs an extractor↔critic loop — we surface a pass count.
const LOOPING = new Set(["summarize", "gap"]);

function bareNode(ev: RunEvent): string {
  return ev.gnode || ev.node || "";
}

// Which top-level stage nodes have finished, in order of arrival.
function completedKeys(events: RunEvent[]): Set<string> {
  const done = new Set<string>();
  for (const ev of events) {
    if (ev.kind !== "node") continue;
    const n = bareNode(ev);
    if (n && !n.includes(":")) done.add(n);
  }
  return done;
}

// Count critic passes per looping stage (e.g. "summarize:critic" ticks).
function passCounts(events: RunEvent[]): Record<string, number> {
  const counts: Record<string, number> = {};
  for (const ev of events) {
    if (ev.kind !== "node") continue;
    const n = bareNode(ev);
    const [parent, child] = n.split(":");
    if (child && child.includes("critic")) counts[parent] = (counts[parent] || 0) + 1;
  }
  return counts;
}

// --- raw log line (kept for the collapsible detail view) ---------------------
const STEP_LABEL: Record<string, string> = {
  fetch: "Fetch paper", extract: "Extract sections", summarize: "Summarize", gap: "Extract gaps",
  assemble: "Assemble", enter: "Load knowledge base", generate: "Generate ideas", store: "Store ideas",
};

function eventLine(ev: RunEvent): { glyph: string; text: string; sub?: string } | null {
  const node = bareNode(ev);
  if (ev.kind === "node") {
    if (!node.includes(":")) return { glyph: "✓", text: STEP_LABEL[node] || node };
    return null;
  }
  if (ev.kind === "llm") {
    const dur = ev.duration_s != null ? ` · ${ev.duration_s}s` : "";
    return { glyph: "·", text: node, sub: `${ev.name || "llm call"}${dur}` };
  }
  if (ev.kind === "event") return { glyph: "›", text: ev.name || "note", sub: node };
  return null;
}

export function RunTranscript({ run }: { run: RunState }) {
  const { startPaperRun, uploadSource } = useWorkspace();
  const [showLog, setShowLog] = useState(false);
  const [uploading, setUploading] = useState(false);
  const logRef = useRef<HTMLDivElement>(null);

  const running = run.status === "running";
  const paused = run.status === "ambiguous" || run.status === "needs_source";
  const stages = run.kind === "idea" ? IDEA_STAGES : PAPER_STAGES;
  const done = completedKeys(run.events);
  const passes = passCounts(run.events);

  // The active stage is the first not-yet-completed one while the run is live.
  let activeIdx = -1;
  if (running) {
    activeIdx = stages.findIndex((s) => !done.has(s.key));
    if (activeIdx === -1) activeIdx = stages.length - 1;
  }

  useEffect(() => {
    if (showLog && logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [run.events.length, showLog]);

  const logLines = run.events.map(eventLine).filter(Boolean) as NonNullable<ReturnType<typeof eventLine>>[];

  return (
    <div className="run-panel">
      <div className="run-panel-head">
        <span className={"status-led " + (running || paused ? "status-led--amber" : run.status === "error" ? "status-led--red" : "status-led--green")}></span>
        <span className="run-panel-title">
          {running
            ? `Running ${run.kind === "idea" ? "idea" : "paper"} agent`
            : run.status === "ambiguous" ? "Which paper did you mean?"
              : run.status === "needs_source" ? "Full text unavailable"
                : run.status === "error" ? "Run failed" : "Run complete"}
        </span>
        {run.query && <span className="run-panel-query">{run.query}</span>}
      </div>

      <ol className="run-pipe">
        {stages.map((s, i) => {
          const isDone = done.has(s.key);
          const isActive = i === activeIdx && !isDone;
          const isError = run.status === "error" && isActive;
          const state = isError ? "error" : isDone ? "done" : isActive ? "active" : "todo";
          const n = passes[s.key] || 0;
          return (
            <li key={s.key} className={"run-stage run-stage--" + state}>
              <span className="run-stage-node">
                {isDone ? <Icon name="check" size={13} />
                  : isError ? <Icon name="alert" size={13} />
                    : isActive ? <span className="run-stage-spin" />
                      : <span className="run-stage-dot" />}
              </span>
              <div className="run-stage-body">
                <div className="run-stage-label">
                  {s.label}
                  {LOOPING.has(s.key) && n > 0 && (
                    <span className="run-stage-pass">{isActive ? "refining · " : ""}{n} pass{n === 1 ? "" : "es"}</span>
                  )}
                </div>
                {(isActive || (!isDone && s.hint)) && s.hint && <div className="run-stage-hint">{s.hint}</div>}
              </div>
            </li>
          );
        })}
      </ol>

      {run.status === "error" && run.error && (
        <p className="run-error">{run.error}</p>
      )}
      {run.status === "done" && (
        <p className="run-done">
          {run.kind === "idea"
            ? `${run.resultIdeaIds.length} idea${run.resultIdeaIds.length === 1 ? "" : "s"} added to the workspace.`
            : run.resultPaperId
              ? "Knowledge base updated — opening the new paper."
              : "Knowledge base updated."}
        </p>
      )}

      {run.status === "ambiguous" && (
        <div className="run-resolve">
          <p className="run-note">
            No exact title match. Pick the paper you meant — the search re-runs with its identifier.
          </p>
          <div className="run-candidates">
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
                  onClick={() => id && startPaperRun(id)}
                >
                  <div className="candidate-title">{c.title || "Untitled"}</div>
                  <div className="candidate-meta">
                    {(c.authors?.slice(0, 3).join(", ")) || "Unknown authors"}
                    {c.year ? ` · ${c.year}` : ""}
                  </div>
                  <div className="candidate-id">
                    {idLabel}{c.source ? ` · ${c.source}` : ""}
                  </div>
                </button>
              );
            })}
          </div>
        </div>
      )}

      {run.status === "needs_source" && (
        <div className="run-resolve">
          <p className="run-note">
            The paper resolved, but its full text couldn't be downloaded (restricted or paywalled).
            Add a PDF or LaTeX source to continue the pipeline.
          </p>
          <SourceDropZone
            busy={uploading}
            onFile={(file) => {
              if (!run.resultPaperId || !run.query) return;
              setUploading(true);
              uploadSource(run.resultPaperId, file, run.query).catch(() => setUploading(false));
            }}
          />
        </div>
      )}

      {logLines.length > 0 && (
        <button className="run-log-toggle" onClick={() => setShowLog((v) => !v)}>
          <Icon name={showLog ? "chevron-down" : "chevron-right"} size={13} />
          {showLog ? "Hide" : "Show"} detailed log
        </button>
      )}
      {showLog && (
        <div className="run-transcript" ref={logRef}>
          {logLines.map((l, i) => (
            <div key={i} className="run-line" style={{ opacity: l.glyph === "·" || l.glyph === "›" ? 0.72 : 1 }}>
              <span className="run-glyph" style={{ color: l.glyph === "✓" ? "var(--accent-green)" : "var(--accent)" }}>{l.glyph}</span>
              <span className="run-text">{l.text}</span>
              {l.sub && <span className="run-sub">{l.sub}</span>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
