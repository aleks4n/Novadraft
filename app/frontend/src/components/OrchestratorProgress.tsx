import { useState } from "react";
import { StepArgs, useToolSpecs } from "./PlanReview";
import { NodeInspector, ThoughtTree } from "./ThoughtTree";
import { treeForRun } from "../lib/thoughts";
import { progressForRun } from "../lib/orchestratorProgress";
import { useWorkspace } from "../lib/store";
import type { RunState, ThoughtNode } from "../lib/types";

function timeLabel(value: string | null): string {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
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
