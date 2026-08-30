import { useState } from "react";
import { StepArgs, useToolSpecs } from "./PlanReview";
import { NodeInspector, ThoughtTree } from "./ThoughtTree";
import { treeForRun } from "../lib/thoughts";
import { progressForRun } from "../lib/orchestratorProgress";
import type { StepHandover } from "../lib/orchestratorProgress";
import { useWorkspace } from "../lib/store";
import type { RunState, ThoughtNode } from "../lib/types";

function timeLabel(value: string | null): string {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

/**
 * What a step was handed, under the step it was handed to.
 *
 * Collapsed by default and summarised by counts: the question "did it have the paper"
 * is answered by the summary line alone, and only the follow-up — "then why is the
 * candidate wrong" — needs the literal prompt text underneath.
 */
function StepContext({ handover }: { handover: StepHandover }) {
  const parts = [
    [handover.gaps.length, "gap"],
    [handover.papers.length, "paper"],
    [handover.relatedIdeas.length, "related idea"],
    [handover.userInput.length, "user input"],
  ] as const;
  const summary = parts
    .filter(([count]) => count > 0)
    .map(([count, label]) => `${count} ${label}${count === 1 ? "" : "s"}`)
    .join(" · ") || "nothing loaded";
  const notLoaded = Object.entries(handover.catalogCounts)
    .filter(([, count]) => count > 0)
    .map(([kind, count]) => `${count} ${kind}`)
    .join(", ");

  return (
    <details className="step-context">
      <summary className="step-context-summary">
        Context it had: {summary}
        {notLoaded && <span className="step-context-rest"> · {notLoaded} not loaded</span>}
      </summary>
      <div className="step-context-body">
        {handover.gaps.length > 0 && <div><b>gaps:</b> {handover.gaps.join(", ")}</div>}
        {handover.papers.length > 0 && <div><b>papers:</b> {handover.papers.join(", ")}</div>}
        {handover.relatedIdeas.length > 0 && (
          <div><b>related ideas:</b> {handover.relatedIdeas.join(", ")}</div>
        )}
        {handover.userInput.length > 0 && (
          <div><b>from you:</b> {handover.userInput.join(", ")}</div>
        )}
        {handover.missingIds.length > 0 && (
          <div className="step-context-missing">
            <b>could not load:</b> {handover.missingIds.join(", ")}
          </div>
        )}
        <details className="step-context-raw">
          <summary>The exact text it was given ({handover.chars.toLocaleString()} chars)</summary>
          <pre>{handover.markdown}</pre>
        </details>
      </div>
    </details>
  );
}

export function OrchestratorProgress({ run }: { run: RunState }) {
  const { cancelRun, isCancelling, restartRun } = useWorkspace();
  const [selected, setSelected] = useState<ThoughtNode | null>(null);
  const specs = useToolSpecs();
  const progress = progressForRun(run);
  const tree = treeForRun(run);
  const thinkerTree = tree ? {
    ...tree,
    nodes: tree.nodes.filter((node) => node.kind !== "root"),
    edges: tree.edges.filter((edge) =>
      tree.nodes.some((node) => node.id === edge.from && node.kind !== "root")
      && tree.nodes.some((node) => node.id === edge.to && node.kind !== "root")),
  } : null;
  const visibleSelection = selected?.kind === "root" ? null : selected;
  const live = run.status === "running";

  return (
    <div className="live-thoughts orchestrator-progress">
      <div className="live-thoughts-head">
        {live && <span className="sidebar-spin" />}
        <span className="live-thoughts-label">{progress.phase}</span>
        {progress.updatedAt && (
          <span className="orchestrator-updated">updated {timeLabel(progress.updatedAt)}</span>
        )}
        {live && (
          <button className="btn btn--ghost btn--sm" onClick={() => cancelRun(run.id)}
            disabled={isCancelling(run.id)}>
            {isCancelling(run.id) ? "Cancelling…" : "Cancel"}
          </button>
        )}
        {(run.status === "error" || run.status === "cancelled" || run.status === "interrupted") && (
          <button className="btn btn--ghost btn--sm" onClick={() => restartRun(run.id)}>
            Retry
          </button>
        )}
      </div>

      {progress.goal && <p className="orchestrator-goal">{progress.goal}</p>}

      {progress.steps.length > 0 ? (
        <ol className="orchestrator-steps">
          {progress.steps.map((step) => (
            <li className={`orchestrator-step orchestrator-step--${step.status}`} key={step.number}>
              <span className="orchestrator-step-mark" aria-label={step.status}>
                {step.status === "active" ? <span className="sidebar-spin" />
                  : step.status === "done" ? "✓"
                  : step.status === "failed" ? "!" : step.number}
              </span>
              <span className="orchestrator-step-copy">
                <span className="orchestrator-step-agent">{step.tool.split("_").join(" ")}</span>
                <span className="orchestrator-step-title" title={step.title}>{step.title}</span>
                {/* What the subagent is actually called with — the plan's arguments for
                    this step, so "creative thinker" is not the whole story. */}
                <StepArgs step={{ tool: step.tool, args: step.args, why: step.why,
                  needs: step.needs }} specs={specs} />
                {/* And what it could see while doing it — a different question from the
                    arguments above, and the one a thin result usually turns out to be
                    about. */}
                {step.handover && <StepContext handover={step.handover} />}
                {step.error && <span className="orchestrator-step-error">{step.error}</span>}
              </span>
            </li>
          ))}
        </ol>
      ) : (
        <div className="orchestrator-plan-wait">
          <span className={live ? "sidebar-spin" : "orchestrator-dot"} />
          The planned steps will appear here as soon as planning finishes.
        </div>
      )}

      <div className="orchestrator-current">
        <span className="orchestrator-current-label">Now</span>
        <span>{progress.detail}</span>
      </div>

      {progress.llm.calls > 0 && (
        <div className="orchestrator-telemetry" aria-label="LLM activity">
          <span title="Logical chat calls started">LLM calls <b>{progress.llm.calls}</b></span>
          <span title="Requests sent to the model provider, including retries and structured re-asks">
            provider attempts <b>{progress.llm.providerAttempts}</b>
          </span>
          <span title="Retries after transient provider errors">retries <b>{progress.llm.retries}</b></span>
          <span title="Logical Investigator agent invocations started">
            investigator calls <b>{progress.llm.investigatorCalls}</b>
          </span>
          {progress.llm.activeInvestigatorCalls > 0 && (
            <span className="orchestrator-telemetry-live">
              <span className="sidebar-spin" /> {progress.llm.activeInvestigatorCalls} investigator active
            </span>
          )}
          {progress.llm.structuredReasks > 0 && (
            <span title="Extra calls made because structured output was missing">
              format re-asks <b>{progress.llm.structuredReasks}</b>
            </span>
          )}
          {progress.llm.failedAttempts > 0 && (
            <span className="orchestrator-telemetry-warning" title="Provider attempts that returned an error">
              failed attempts <b>{progress.llm.failedAttempts}</b>
            </span>
          )}
          {progress.llm.activeCalls > 0 && (
            <span className="orchestrator-telemetry-live">
              <span className="sidebar-spin" /> {progress.llm.activeCalls} active
            </span>
          )}
          {progress.llm.totalDurationSeconds > 0 && (
            <span title="Combined duration of completed logical LLM calls">
              completed in <b>{progress.llm.totalDurationSeconds.toFixed(1)}s</b>
            </span>
          )}
        </div>
      )}

      {run.error && <p className="run-error">{run.error}</p>}

      {tree && (
        <div className="orchestrator-thinker">
          <div className="orchestrator-thinker-head">
            Creative thinker
            {thinkerTree && thinkerTree.nodes.length > 0
              ? ` · ${thinkerTree.nodes.length} candidate${thinkerTree.nodes.length === 1 ? "" : "s"}`
              : ""}
          </div>
          {thinkerTree && thinkerTree.nodes.length > 0 ? (
            <ThoughtTree run={thinkerTree} selected={visibleSelection} onSelect={setSelected} />
          ) : (
            <p className="live-thoughts-wait">
              Candidate branches will appear here as they are generated.
            </p>
          )}
          {visibleSelection && (
            <NodeInspector node={visibleSelection} onClose={() => setSelected(null)} />
          )}
        </div>
      )}
    </div>
  );
}
