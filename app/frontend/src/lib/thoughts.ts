// The tree of thoughts of one run, from whichever source has it.
//
// A creative-thinker run streams its tree as it grows — `tools/trace.py` emits a
// `branch created` the moment a node is minted, then `branch scored` and
// `branch <status>` as the selector judges it. So the tree is watchable live, not
// only after the fact, which is the whole point of showing it beside the chat.
//
// Once the run ends the authoritative copy is the run entry the envelope carries
// (`envelope.idea.runs[-1]`) — same nodes, plus the outcome. Prefer it; fall back
// to the events while the run is still going.

import type { Envelope, IdeaRun, RunEvent, RunState, ThoughtNode } from "./types";

const EMPTY_OUTCOME: IdeaRun["outcome"] = {
  status: "completed", reason: "", limit: null,
  acceptedNode: null, acceptedNodes: [], escalation: null, escalationNode: null,
  escalationDiagnosis: "", answer: "", proposedChanges: [], truncated: false,
  summary: { objective: "", accepted: "", rejected: "", open: [] },
};

function num(v: unknown): number {
  return typeof v === "number" ? v : 0;
}

function str(v: unknown): string {
  return typeof v === "string" ? v : "";
}

/** Replay a run's `branch …` trace events into the tree they describe. */
export function treeFromEvents(events: RunEvent[]): IdeaRun | null {
  const byId = new Map<string, ThoughtNode>();
  const order: string[] = [];
  const edges: IdeaRun["edges"] = [];

  for (const ev of events) {
    if (ev.kind !== "event" || !ev.name?.startsWith("branch ")) continue;
    const d = (ev.data ?? {}) as Record<string, unknown>;
    const id = str(d["node_id"]);
    if (!id) continue;

    if (ev.name === "branch created") {
      const parentId = str(d["parent_id"]) || null;
      byId.set(id, {
        id,
        parentId,
        text: str(d["text"]),
        title: str(d["title"]),
        style: str(d["style"]) || "neutral",
        score: null,
        status: "kept",
        kind: (str(d["kind"]) || "branch") as ThoughtNode["kind"],
        depth: num(d["depth"]),
        comparison: "",
        question: null,
      });
      order.push(id);
      if (parentId) edges.push({ from: parentId, to: id, type: "expanded-into" });
      continue;
    }

    const node = byId.get(id);
    if (!node) continue;   // a status for a node whose creation we missed
    if (ev.name === "branch scored") {
      node.score = typeof d["score"] === "number" ? (d["score"] as number) : node.score;
      node.comparison = str(d["comparison"]) || node.comparison;
    } else {
      // `branch kept` / `branch pruned` / `branch rejected` / `branch accepted`
      const status = ev.name.slice("branch ".length) as ThoughtNode["status"];
      if (["kept", "pruned", "rejected", "accepted"].includes(status)) node.status = status;
      node.comparison = str(d["comparison"]) || node.comparison;
    }
  }

  if (!order.length) return null;
  return {
    runId: "live", sessionId: null, timestamp: "", mode: "", width: null, depth: null,
    model: "", styles: [], task: "", triggeringAction: "", contextIds: [],
    nodes: order.map((id) => byId.get(id)!),
    edges,
    outcome: EMPTY_OUTCOME,
  };
}

/** The run entry an envelope carries — the authoritative tree, with the outcome. */
export function treeFromEnvelope(envelope: Envelope): IdeaRun | null {
  const runs = (envelope.idea.runs as unknown[] | undefined) || [];
  const raw = runs[runs.length - 1] as Record<string, any> | undefined;
  if (!raw) return null;
  const header = raw.header || {};
  const outcome = raw.outcome || {};
  const summary = typeof outcome.summary === "string"
    ? { objective: outcome.summary } : (outcome.summary || {});
  return {
    runId: str(raw.run_id) || envelope.run_id,
    sessionId: raw.session_id ?? null,
    timestamp: str(raw.timestamp),
    mode: str(header.mode),
    width: header.width ?? null,
    depth: header.depth ?? null,
    model: str(header.model),
    styles: header.agent_styles || [],
    task: str(header.task),
    triggeringAction: str(header.triggering_action),
    contextIds: header.context_ids || [],
    nodes: (raw.nodes || []).map((n: Record<string, any>) => ({
      id: str(n.node_id), parentId: n.parent_id ?? null, text: str(n.text),
      title: str(n.title), style: str(n.style) || "neutral",
      score: typeof n.score === "number" ? n.score : null,
      status: (n.status || "kept") as ThoughtNode["status"],
      kind: (n.kind || "branch") as ThoughtNode["kind"],
      depth: num(n.depth), comparison: str(n.comparison), question: n.question ?? null,
    })),
    edges: (raw.edges || []).map((e: Record<string, any>) => ({
      from: str(e.from ?? e.from_node), to: str(e.to ?? e.to_node),
      type: e.type || "expanded-into",
    })),
    outcome: {
      status: outcome.status || envelope.status,
      reason: str(outcome.reason) || envelope.status_reason,
      limit: outcome.limit ?? envelope.limit,
      acceptedNode: outcome.accepted_node ?? null,
      acceptedNodes: outcome.accepted_nodes || [],
      proposedChanges: outcome.proposed_changes || [],
      escalation: outcome.escalation ?? null,
      escalationNode: outcome.escalation_node ?? null,
      escalationDiagnosis: str(outcome.escalation_diagnosis),
      answer: str(outcome.answer) || envelope.answer,
      truncated: Boolean(outcome.truncated),
      summary: {
        objective: str(summary.objective), accepted: str(summary.accepted),
        rejected: str(summary.rejected),
        open: Array.isArray(summary.open) ? summary.open
          : summary.open ? [String(summary.open)] : [],
      },
    },
  };
}

/** The tree to show for a run: the finished entry if it landed, else the live one. */
export function treeForRun(run: RunState | undefined): IdeaRun | null {
  if (!run) return null;
  if (run.envelope) {
    const finished = treeFromEnvelope(run.envelope);
    if (finished) return finished;
  }
  return treeFromEvents(run.events);
}
