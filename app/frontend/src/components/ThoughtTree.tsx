// One run's tree of thoughts, drawn as a tree: nodes on depth rows, each joined
// to its parent by a branch line.
//
// Shared by two surfaces: the decision-graph window on an idea's Thinking tab
// (every run, end to end) and the live tree beside the session chat (the current
// run, growing as it goes). Same reading aids in both — colour by status, a style
// badge per node, and truncation marked, so a partial exploration never looks
// finished.
//
// Nodes carry no score: a round keeps one branch, so the inspector's comparison
// line is what says why a branch won or died.
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Icon } from "./Icon";
import type { IdeaRun, ThoughtNode } from "../lib/types";

const NODE_W = 220;
const NODE_H = 108;
const COL_GAP = 16;
const ROW_GAP = 46;
const PAD = 10;
// In a session window the tree is drawn at whatever scale makes the whole width
// fit — a five-wide tree at full size is mostly off-screen, and the shape is the
// point. It stops shrinking at MIN_FIT, below which the cards stop being
// readable and horizontal scrolling is the better answer.
const MIN_FIT = 0.42;

/** The width available to the tree, tracked live so a splitter drag re-fits it.
 *
 *  Measured after *every* render rather than through a ResizeObserver: each drag
 *  that changes this width — the chat splitter, a panel divider, the nav rail —
 *  re-renders this tree anyway, and the observer turned out not to fire at all in
 *  one of the shells this runs in. The window listener covers the one case that
 *  re-renders nothing. */
function useMeasuredWidth() {
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const w = Math.round(el.clientWidth);
    // Guarded so the state write cannot loop: whole pixels only, and a change has
    // to be worth more than the noise of a re-layout before it re-renders.
    setWidth((cur) => (Math.abs(cur - w) > 2 ? w : cur));
  });
  useEffect(() => {
    const onResize = () => { if (ref.current) setWidth(ref.current.clientWidth); };
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);
  return [ref, width] as const;
}

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
  run, showPruned = true, selected, onSelect, fit = false,
}: {
  run: IdeaRun;
  showPruned?: boolean;
  selected: ThoughtNode | null;
  onSelect: (n: ThoughtNode) => void;
  /** Scale the whole tree down to the container's width (session windows). */
  fit?: boolean;
}) {
  const nodes = showPruned ? run.nodes : run.nodes.filter((n) => n.status !== "pruned");
  const { placed, at, width, height } = useMemo(() => layout(nodes), [nodes]);
  const [boxRef, boxW] = useMeasuredWidth();

  // Never scale *up*: a two-node tree at 3× would look like a mistake.
  const scale = fit && boxW > 0 ? Math.min(1, Math.max(MIN_FIT, boxW / width)) : 1;

  if (!placed.length) {
    return <div className="empty-state empty-state--inline">Nothing to show at this filter.</div>;
  }

  return (
    <div className={"tt" + (fit ? " tt--fit" : "")} ref={boxRef}>
      {/* The stage carries the *scaled* box so the scrollport and the space the
          tree occupies in the column agree; the inner layer keeps its true
          geometry and is transformed as a whole. */}
      <div className="tt-stage" style={{ width: width * scale, height: height * scale }}>
        <div style={{ width, height, transform: `scale(${scale})`, transformOrigin: "top left" }}>
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
