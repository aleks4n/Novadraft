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

// A pipeline stage. ``match`` is the ``gnode`` (get_graph xray id) whose completion
// marks the stage done. In the full pipeline the paper agent is a mounted subgraph,
// so its stages stream namespaced under their owner (``paper:fetch``, ``paper:gap``,
// …); the director is one subgraph node (``director``); the validator fans out over
// the novel gaps as repeated ``validate_gap`` nodes; ``collect`` is the final save.
interface Stage { key: string; label: string; hint?: string; match: string; loop?: "summarize" | "gap" }

const PAPER_STAGES: Stage[] = [
  { key: "fetch", match: "paper:fetch", label: "Fetch paper", hint: "Resolve & download the source" },
  { key: "extract", match: "paper:extract", label: "Extract sections", hint: "Parse the full text" },
  { key: "summarize", match: "paper:summarize", label: "Summarize & verify", hint: "Draft and self-check the summary", loop: "summarize" },
  { key: "gap", match: "paper:gap", label: "Mine research gaps", hint: "Find the gaps the paper states", loop: "gap" },
  { key: "director", match: "director", label: "Deduplicate gaps", hint: "Check new gaps against the master list" },
  { key: "validate", match: "validate_gap", label: "Validate gaps", hint: "Search prior art for each novel gap" },
  { key: "rank", match: "rank_gap", label: "Rank gaps", hint: "Score each open opportunity" },
  { key: "save", match: "collect", label: "Save to knowledge base" },
];

// The creative thinker: prepare → (generate | refine) → assemble. Refinement is a
// mounted subgraph, so its levels stream as repeated `expand` / `evaluate` nodes
// under it, and `writeback` closes the tree before `assemble` builds the envelope.
const IDEA_STAGES: Stage[] = [
  { key: "prepare", match: "prepare", label: "Prepare", hint: "Copy the idea, render the context and prior ground" },
  { key: "generate", match: "generate", label: "Generate candidates", hint: "One styled thinker per branch, deduplicated and ranked" },
  { key: "expand", match: "expand", label: "Expand the tree", hint: "Ways of doing the task from here" },
  { key: "evaluate", match: "evaluate", label: "Evaluate the frontier", hint: "One call over every branch" },
  { key: "writeback", match: "writeback", label: "Write back", hint: "Answer the task and apply the edits" },
  { key: "assemble", match: "assemble", label: "Assemble the envelope" },
];

// Resuming ingestion runs just the director+validator tail (no paper stages). The
// director step may be skipped when nothing is in pending/ — the generalized
// stageDone() marks it done once a later stage is reached.
const RESUME_STAGES: Stage[] = [
  { key: "plan", match: "plan", label: "Plan resume", hint: "Find where each gap stopped" },
  { key: "director", match: "director", label: "Deduplicate gaps", hint: "Check against the master list" },
  { key: "validate", match: "validate_gap", label: "Validate gaps", hint: "Search prior art for each gap" },
  { key: "rank", match: "rank_gap", label: "Rank gaps", hint: "Score each open opportunity" },
  { key: "save", match: "collect", label: "Save to knowledge base" },
];

function bareNode(ev: RunEvent): string {
  return ev.gnode || ev.node || "";
}

// The set of gnodes whose node-completion event has arrived.
function completedGnodes(events: RunEvent[]): Set<string> {
  const done = new Set<string>();
  for (const ev of events) {
    if (ev.kind !== "node") continue;
    const n = bareNode(ev);
    if (n) done.add(n);
  }
  return done;
}

// How many gaps each sequential loop has finished (one node completion per gap).
function nodeCount(events: RunEvent[], gnode: string): number {
  return events.filter((ev) => ev.kind === "node" && bareNode(ev) === gnode).length;
}

// Count named trace events (the per-gap progress notes the parallel validate/rank
// phases emit: gap_validate_start / gap_validated / gap_rank_start / gap_ranked).
function traceCount(events: RunEvent[], name: string): number {
  return events.filter((ev) => ev.kind === "event" && ev.name === name).length;
}

// Per-gap progress of a fan-out phase: done + currently in flight. The phase node
// fires once for the whole batch, so gap-level progress comes from the trace
// events; the node-count fallback keeps old (sequential-loop) run logs rendering.
function fanoutProgress(events: RunEvent[], phase: "validate" | "rank", loopGnode: string) {
  const doneName = phase === "validate" ? "gap_validated" : "gap_ranked";
  const started = traceCount(events, `gap_${phase}_start`);
  const done = traceCount(events, doneName) + traceCount(events, `gap_${phase}_failed`);
  return {
    done: Math.max(done, started ? 0 : nodeCount(events, loopGnode)),
    inFlight: Math.max(0, started - done),
  };
}

// Critic passes per looping stage. Both loops collapse to gnode ``paper:critic``,
// so we attribute each pass by its namespace (which still carries summarize/gap).
function passCounts(events: RunEvent[]): Record<string, number> {
  const counts: Record<string, number> = { summarize: 0, gap: 0 };
  for (const ev of events) {
    if (ev.kind !== "node") continue;
    const isCritic = (ev.node || "").includes("critic") || bareNode(ev).endsWith(":critic");
    if (!isCritic) continue;
    const ns = ev.ns || "";
    if (ns.includes("summarize")) counts.summarize += 1;
    else if (ns.includes("gap")) counts.gap += 1;
  }
  return counts;
}

// --- raw log line (kept for the collapsible detail view) ---------------------
const STEP_LABEL: Record<string, string> = {
  fetch: "Fetch paper", extract: "Extract sections", summarize: "Summarize", gap: "Extract gaps",
  assemble: "Assemble", enter: "Load knowledge base", generate: "Generate ideas", store: "Store ideas",
  paper: "Paper agent", director: "Deduplicate gaps", validate_gap: "Validate gap",
  rank_gap: "Rank gap", collect: "Save to knowledge base",
};

// Is this stage complete? A stage is done if any *later* stage has been reached
// (so a skipped stage — e.g. the director when nothing's in pending — still ticks),
// or its own node fired. The fan-out validator stage is special: a single gap
// finishing early mustn't tick it, so it's only done once a later stage is reached.
// Aggregate fan-out stages (validate, rank) fire once per gap, so an early gap
// finishing mustn't tick the whole stage — they're done only once a later stage
// has been reached.
const AGGREGATE_KEYS = new Set(["validate", "rank"]);
function stageDone(i: number, stages: Stage[], done: Set<string>): boolean {
  for (let j = i + 1; j < stages.length; j++) if (done.has(stages[j].match)) return true;
  if (AGGREGATE_KEYS.has(stages[i].key)) return false;
  return done.has(stages[i].match);
}

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
  const { startPaperRun, uploadSource, cancelRun, restartRun, deleteRun, isCancelling } = useWorkspace();
  const [showLog, setShowLog] = useState(false);
  const [uploading, setUploading] = useState(false);
  const logRef = useRef<HTMLDivElement>(null);

  const running = run.status === "running";
  const cancelling = running && isCancelling(run.id);
  const paused = run.status === "ambiguous" || run.status === "needs_source";
  const resumable = run.status === "cancelled" || run.status === "interrupted" || run.status === "error";
  const stages = run.kind === "idea" ? IDEA_STAGES : run.kind === "resume" ? RESUME_STAGES : PAPER_STAGES;
  const done = completedGnodes(run.events);
  const passes = passCounts(run.events);
  const fanout: Record<string, { done: number; inFlight: number }> = {
    validate: fanoutProgress(run.events, "validate", "validate_gap"),
    rank: fanoutProgress(run.events, "rank", "rank_gap"),
  };

  // The active stage is the first not-yet-completed one while the run is live.
  let activeIdx = -1;
  if (running) {
    activeIdx = stages.findIndex((_, i) => !stageDone(i, stages, done));
    if (activeIdx === -1) activeIdx = stages.length - 1;
  }

  useEffect(() => {
    if (showLog && logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [run.events.length, showLog]);

  const logLines = run.events.map(eventLine).filter(Boolean) as NonNullable<ReturnType<typeof eventLine>>[];

  return (
    <div className="run-panel">
      <div className="run-panel-head">
        <span className={"status-led " + (running || paused ? "status-led--amber" : run.status === "error" ? "status-led--red" : resumable ? "status-led--amber" : "status-led--green")}></span>
        <span className="run-panel-title">
          {cancelling ? "Cancelling — stopping after the current step…"
            : running
            ? run.kind === "resume" ? "Resuming gap ingestion"
              : `Running the ${run.kind === "idea" ? "creative thinker" : "paper agent"}`
            : run.status === "ambiguous" ? "Which paper did you mean?"
              : run.status === "needs_source" ? "Full text unavailable"
                : run.status === "error" ? "Run failed"
                  : run.status === "cancelled" ? "Run cancelled"
                    : run.status === "interrupted" ? "Run interrupted"
                      : "Run complete"}
        </span>
        {run.query && <span className="run-panel-query">{run.query}</span>}
        <span className="run-panel-actions">
          {running && (
            <button className="btn btn--ghost btn--sm" title="Stop after the current step"
              onClick={() => cancelRun(run.id)} disabled={cancelling}>
              <Icon name={cancelling ? "clock" : "x"} /> {cancelling ? "Cancelling…" : "Cancel"}
            </button>
          )}
          {resumable && (
            <button className="btn btn--ghost btn--sm" title="Re-run — completed stages are skipped"
              onClick={() => restartRun(run.id)}>
              <Icon name="play" /> Resume
            </button>
          )}
          {!running && (
            <button className="btn btn--ghost btn--sm" title="Dismiss this run"
              onClick={() => deleteRun(run.id)}>
              <Icon name="x" /> Dismiss
            </button>
          )}
        </span>
      </div>

      {run.note === "duplicate" && (
        <p className="run-note">
          This paper was already in the library — completed stages were skipped.
        </p>
      )}

      <ol className="run-pipe">
        {stages.map((s, i) => {
          const isDone = stageDone(i, stages, done);
          const isActive = i === activeIdx && !isDone;
          const isError = run.status === "error" && isActive;
          const state = isError ? "error" : isDone ? "done" : isActive ? "active" : "todo";
          const n = s.loop ? passes[s.loop] || 0 : 0;
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
                  {s.loop && n > 0 && (
                    <span className="run-stage-pass">{isActive ? "refining · " : ""}{n} pass{n === 1 ? "" : "es"}</span>
                  )}
                  {AGGREGATE_KEYS.has(s.key) && (fanout[s.key].done > 0 || fanout[s.key].inFlight > 0) && (
                    <span className="run-stage-pass">
                      {fanout[s.key].inFlight > 0 && `${fanout[s.key].inFlight} in parallel`}
                      {fanout[s.key].inFlight > 0 && fanout[s.key].done > 0 && " · "}
                      {fanout[s.key].done > 0 && `${fanout[s.key].done} gap${fanout[s.key].done === 1 ? "" : "s"} done`}
                    </span>
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
            // The thinker writes nothing — it returns a modified copy and its
            // proposals. Saying "added to the workspace" here would be a lie.
            ? "Envelope returned — nothing is written until you accept something."
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
                  onClick={() => { if (id) { startPaperRun(id); deleteRun(run.id); } }}
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
