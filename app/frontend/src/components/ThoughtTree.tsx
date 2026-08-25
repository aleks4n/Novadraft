// One run's tree of thoughts, drawn as a tree: nodes on depth rows, each joined
// to its parent by a branch line.
//
// It was a stack of rows before, which showed the same nodes and none of the
// shape — and the shape is the content here. Which branch a thought came out of,
// where the tree widened, which sub-tree got pruned whole: all of that is the
// edges, and a list has no edges.
//
// Shared by the two surfaces the vault's *UI Surfaces* note asks for: the
// decision-graph window on an idea's Thinking tab (every run, end to end) and the
// live tree beside the session chat (the current run, growing as it goes). Same
// reading aids in both — colour by status, a style badge per node, and truncation
// marked, because a partial exploration must never look finished.
import { useLayoutEffect, useMemo, useRef, useState } from "react";
import { Icon } from "./Icon";
import type { IdeaRun, ThoughtNode } from "../lib/types";

// Small on purpose: the tree is read as a shape first and a text second, and a
// node big enough to hold its whole thought makes a tree of eight unreadable.
// The full text is one click away in the inspector.
const NODE_W = 132;
// Tall enough for the style badge plus two clamped title lines; anything less
// clips the second line, which is exactly the line that says what the branch is.
const NODE_H = 48;
const COL_GAP = 12;
const ROW_GAP = 34;
const PAD = 10;

interface Placed {
  node: ThoughtNode;
  x: number;
  y: number;
}

/** Tidy-tree layout: leaves take sequential slots, a parent centres over its
 *  children. Orphans (a node whose parent was filtered out) are treated as roots
 *  so nothing silently disappears from the picture. */
function layout(nodes: ThoughtNode[]) {
  const present = new Set(nodes.map((n) => n.id));
  const childrenOf = new Map<string, ThoughtNode[]>();
  const roots: ThoughtNode[] = [];
  for (const n of nodes) {
    const parent = n.parentId && present.has(n.parentId) ? n.parentId : null;
    if (parent) childrenOf.set(parent, [...(childrenOf.get(parent) ?? []), n]);
    else roots.push(n);
  }

  const step = NODE_W + COL_GAP;
  const placed: Placed[] = [];
  const at = new Map<string, Placed>();
  let next = 0;

  const walk = (n: ThoughtNode, depth: number): number => {
    const kids = childrenOf.get(n.id) ?? [];
    let slot: number;
    if (!kids.length) {
      slot = next++;
    } else {
      const centres = kids.map((k) => walk(k, depth + 1));
      slot = (centres[0] + centres[centres.length - 1]) / 2;
    }
    const p = { node: n, x: PAD + slot * step, y: PAD + depth * (NODE_H + ROW_GAP) };
    placed.push(p);
    at.set(n.id, p);
    return slot;
  };
  roots.forEach((r) => walk(r, 0));

  const depth = Math.max(0, ...placed.map((p) => (p.y - PAD) / (NODE_H + ROW_GAP)));
  return {
    placed,
    at,
    width: PAD * 2 + Math.max(step, next * step) - COL_GAP,
    height: PAD * 2 + (depth + 1) * NODE_H + depth * ROW_GAP,
  };
}

export function ThoughtTree({
  run, showPruned = true, selected, onSelect,
}: {
  run: IdeaRun;
  showPruned?: boolean;
  selected: ThoughtNode | null;
  onSelect: (n: ThoughtNode) => void;
}) {
  const nodes = showPruned ? run.nodes : run.nodes.filter((n) => n.status !== "pruned");
  const { placed, at, width, height } = useMemo(() => layout(nodes), [nodes]);
  const wrapRef = useRef<HTMLDivElement>(null);
  const [scale, setScale] = useState(1);

  // Shrink to fit the surface, never past legibility — a session panel is narrow,
  // and the tree grows sideways with every expansion.
  useLayoutEffect(() => {
    const el = wrapRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const fit = () => setScale(Math.min(1, Math.max(0.45, (el.clientWidth - 4) / width)));
    const ro = new ResizeObserver(fit);
    ro.observe(el);
    fit();
    return () => ro.disconnect();
  }, [width]);

  if (!placed.length) {
    return <div className="empty-state empty-state--inline">Nothing to show at this filter.</div>;
  }

  return (
    <div className="tt" ref={wrapRef}>
      <div className="tt-stage" style={{ width: width * scale, height: height * scale }}>
        <div style={{ width, height, transform: `scale(${scale})`, transformOrigin: "0 0" }}>
          <svg className="tt-edges" width={width} height={height}>
            {placed.map(({ node }) => {
              const parent = node.parentId ? at.get(node.parentId) : undefined;
              const self = at.get(node.id)!;
              if (!parent) return null;
              const x1 = parent.x + NODE_W / 2;
              const y1 = parent.y + NODE_H;
              const x2 = self.x + NODE_W / 2;
              const y2 = self.y;
              const mid = y1 + ROW_GAP / 2;
              // Elbow rather than a curve: at this size a bezier between two close
              // rows reads as a smudge, where a right angle reads as structure.
              return (
                <path
                  key={node.id}
                  className={"tt-edge tt-edge--" + node.status}
                  d={`M ${x1} ${y1} V ${mid} H ${x2} V ${y2}`}
                  fill="none"
                />
              );
            })}
          </svg>

          {placed.map(({ node, x, y }) => (
            <button
              key={node.id}
              className={
                "tt-node tt-node--" + node.status +
                (selected?.id === node.id ? " tt-node--selected" : "")
              }
              style={{ left: x, top: y, width: NODE_W, height: NODE_H }}
              onClick={() => onSelect(node)}
              title={node.text || node.title}
            >
              <span className="tt-node-style">{node.style}</span>
              <span className="tt-node-title">
                {node.title || node.text.slice(0, 48) || node.id}
              </span>
              {node.score != null && (
                <span className="tt-node-score">{node.score.toFixed(2)}</span>
              )}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

export function NodeInspector({ node, onClose }: { node: ThoughtNode; onClose: () => void }) {
  return (
    <aside className="thought-inspector">
      <div className="thought-inspector-head">
        <span className={"thought-status thought-status--" + node.status}>{node.status}</span>
        <span className="thought-inspector-style">{node.style}</span>
        <button className="ghost-btn" onClick={onClose}><Icon name="x" /></button>
      </div>
      <div className="thought-inspector-title">{node.title || node.id}</div>
      <p className="thought-inspector-text">{node.text}</p>
      {node.comparison && (
        <div className="thought-inspector-block">
          <div className="card-label">Why it survived / lost</div>
          <p className="card-body">{node.comparison}</p>
        </div>
      )}
      {node.question && (
        <div className="thought-inspector-block">
          <div className="card-label">Question raised</div>
          <p className="card-body">{node.question}</p>
        </div>
      )}
      <div className="thought-inspector-meta">
        <span>{node.kind}</span><span>depth {node.depth}</span>
        {node.score != null && <span>score {node.score.toFixed(2)}</span>}
        <span className="mono">{node.id}</span>
      </div>
    </aside>
  );
}

/** A run that stopped short says so, in both surfaces. */
export function TruncationNote({ run }: { run: IdeaRun }) {
  const s = run.outcome;
  if (s.status === "completed") return null;
  return (
    <div className={"run-band-cut run-band-cut--" + s.status}>
      <Icon name="alert" />
      <span>
        {s.status === "truncated"
          ? `Cut off by ${s.limit || "a budget ceiling"} — these are the last completed round's survivors.`
          : `Stopped undecided — ${s.reason || "the selector could not choose, so every branch is still standing."}`}
      </span>
    </div>
  );
}
