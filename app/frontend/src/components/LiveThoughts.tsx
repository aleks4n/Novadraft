// A run in progress, in the chat: its tree of thoughts as it grows.
//
// This replaced the stage pipeline (prepare → generate → … → assemble). The stages
// say *where* the agent is, which is a progress bar with extra words; the tree says
// *what it is considering*, which is the thing a researcher is actually waiting for.
// Both come from the same stream — `tools/trace.py` emits every node as it is
// minted — so this costs nothing the pipeline was not already paying.
//
// The failure and paused states still get plain text: a run that died owes an
// error, not a diagram.
import { useState } from "react";
import { Icon } from "./Icon";
import { OrchestratorProgress } from "./OrchestratorProgress";
import { NodeInspector, ThoughtTree } from "./ThoughtTree";
import { useMirroredInPanel } from "./session/thoughtsMirror";
import { isOrchestratorRun } from "../lib/orchestratorProgress";
import { treeForRun } from "../lib/thoughts";
import { useWorkspace } from "../lib/store";
import type { RunState, ThoughtNode } from "../lib/types";

export function LiveThoughts({ run }: { run: RunState }) {
  if (isOrchestratorRun(run)) return <OrchestratorProgress run={run} />;
  return <CreativeThoughts run={run} />;
}

function CreativeThoughts({ run }: { run: RunState }) {
  const { cancelRun, isCancelling, restartRun } = useWorkspace();
  const [selected, setSelected] = useState<ThoughtNode | null>(null);
  const tree = treeForRun(run);
  const live = run.status === "running";
  // The Tree of thoughts window is drawing this same tree — don't draw it twice.
  const mirrored = useMirroredInPanel(run.id);

  return (
    <div className="live-thoughts">
      <div className="live-thoughts-head">
        {live && <span className="sidebar-spin" />}
        <span className="live-thoughts-label">
          {live ? "thinking" : run.status === "error" ? "run failed" : run.status}
        </span>
        {tree && (
          <span className="live-thoughts-count">
            {tree.nodes.length} thought{tree.nodes.length === 1 ? "" : "s"}
          </span>
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

      {run.error && <p className="run-error">{run.error}</p>}

      {mirrored ? (
        <p className="live-thoughts-wait">
          {tree ? "Shown in the Tree of thoughts window."
            : "Branches will appear in the Tree of thoughts window."}
        </p>
      ) : tree ? (
        <>
          <ThoughtTree run={tree} selected={selected} onSelect={setSelected} />
          {selected && <NodeInspector node={selected} onClose={() => setSelected(null)} />}
        </>
      ) : (
        <p className="live-thoughts-wait">
          {live
            ? <><Icon name="clock" size={13} /> Reading the context — the first branches appear shortly.</>
            : "No thoughts were recorded for this run."}
        </p>
      )}
    </div>
  );
}
