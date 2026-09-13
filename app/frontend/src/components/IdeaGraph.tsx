// The idea graph — gaps on top, the ideas that attack them below, typed edges
// between them. One renderer, two surfaces: the full-page graph (Ideas header)
// and the compact panel inside a session, where nodes carry context highlights
// and clicking one stages it for the next turn.
//
// Node types are `gap` and `idea`; edges are `addresses` (idea → gap) plus the
// typed idea → idea relations `child-of` / `variant-of` / `merged-into` /
// `inspired-by`. The thinking graph (`runs[]`) is a separate surface, on the idea
// page's Thinking tab.
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import type { Gap, Idea } from "../lib/types";

export type GraphNodeKind = "gap" | "idea";

// How a node is marked in a session: `loaded` is in the run context already,
// `staged` is selected for the *next* run and not yet sent. Two different marks
// on purpose — the difference is the whole point of the staging step.
export type Mark = "loaded" | "staged" | "none";

const NODE_W = 176;
const NODE_H = 64;
const COL_GAP = 26;
const ROW_GAP = 84;
const PAD = 28;

interface Positioned {
  kind: GraphNodeKind;
  id: string;
  title: string;
  sub: string;
  state?: string;
  severity?: string;
  // A gap no idea addresses yet — packed into the band at the top, where its x
  // position is layout, not meaning.
  loose?: boolean;
  x: number;
  y: number;
}

interface Link {
  from: string;             // node key, `${kind}:${id}`
  to: string;
  type: "addresses" | "child-of" | "variant-of" | "merged-into" | "inspired-by";
  rationale: string;
}

const key = (kind: GraphNodeKind, id: string) => `${kind}:${id}`;

// Pointer travel, in px, past which a press-and-release is a pan rather than a click.
const DRAG_SLOP = 4;

const DERIVING = new Set(["child-of", "variant-of", "merged-into"]);

/** Lay the graph out: gaps on top, then ideas layered by derivation depth.
 *
 * Ideas are a tidy tree — leaves take sequential slots, parents centre over their
 * children — and each **addressed** gap centres over the ideas that cite it.
 *
 * Gaps nothing addresses yet are the majority in a young workspace (twenty mined
 * gaps, three ideas), and giving each one a column of its own made the graph four
 * thousand pixels wide to show three edges. They carry no edges, so their x
 * position means nothing: they pack into a wrapped band above the graph instead,
 * where they are still one click from becoming context. */
function layout(gaps: Gap[], ideas: Idea[], viewWidth: number) {
  const ideaById = new Map(ideas.map((i) => [i.id, i]));

  // parent = the first deriving relation whose target is in view.
  const parentOf = new Map<string, string>();
  for (const idea of ideas) {
    const rel = idea.relations.find((r) => DERIVING.has(r.type) && ideaById.has(r.target));
    if (rel) parentOf.set(idea.id, rel.target);
  }
  // A relation cycle would hang the depth walk; drop the back-edge instead.
  for (const id of [...parentOf.keys()]) {
    const seen = new Set([id]);
    let cur = parentOf.get(id);
    while (cur) {
      if (seen.has(cur)) { parentOf.delete(id); break; }
      seen.add(cur);
      cur = parentOf.get(cur);
    }
  }

  const childrenOf = new Map<string, string[]>();
  for (const [child, parent] of parentOf) {
    childrenOf.set(parent, [...(childrenOf.get(parent) ?? []), child]);
  }
  const roots = ideas.filter((i) => !parentOf.has(i.id)).map((i) => i.id);

  const connected = gaps.filter((g) => ideas.some((i) => i.addresses.includes(g.id)));
  const loose = gaps.filter((g) => !connected.includes(g));

  // Order roots by their first addressed gap, so ideas on the same gap sit together.
  const gapOrder = new Map(connected.map((g, i) => [g.id, i]));
  roots.sort((a, b) => {
    const ga = ideaById.get(a)!.addresses.find((x) => gapOrder.has(x));
    const gb = ideaById.get(b)!.addresses.find((x) => gapOrder.has(x));
    return (ga ? gapOrder.get(ga)! : 1e6) - (gb ? gapOrder.get(gb)! : 1e6);
  });

  const step = NODE_W + COL_GAP;
  const slot = new Map<string, number>();
  const depth = new Map<string, number>();
  let next = 0;

  const place = (id: string, d: number): number => {
    depth.set(id, d);
    const kids = childrenOf.get(id) ?? [];
    if (!kids.length) {
      const mine = next++;
      slot.set(id, mine);
      return mine;
    }
    const centres = kids.map((k) => place(k, d + 1));
    const mine = (centres[0] + centres[centres.length - 1]) / 2;
    slot.set(id, mine);
    return mine;
  };
  roots.forEach((r) => place(r, 0));

  // The loose band wraps to whichever is wider: the viewport, or the tree below it.
  const treeWidth = Math.max(step, next * step);
  const bandWidth = Math.max(treeWidth, viewWidth - PAD * 2, step * 3);
  const perRow = Math.max(1, Math.floor(bandWidth / step));
  const bandRows = Math.ceil(loose.length / perRow);
  const LOOSE_H = NODE_H + 10;
  const bandHeight = bandRows ? bandRows * LOOSE_H + 16 : 0;

  const gapY = PAD + bandHeight;
  // With no addressed gaps there is no gap row to leave space for — a young
  // workspace would otherwise show a hand's width of nothing above the ideas.
  const ideaY = gapY + (connected.length ? NODE_H + ROW_GAP : 24);
  const maxDepth = Math.max(0, ...[...depth.values()]);
  const ideaNodes: Positioned[] = ideas.map((i) => ({
    kind: "idea" as const,
    id: i.id,
    title: i.title || i.short || i.id,
    sub: i.oneLiner || i.description || "",
    state: i.state,
    x: PAD + (slot.get(i.id) ?? 0) * step,
    y: ideaY + (depth.get(i.id) ?? 0) * (NODE_H + ROW_GAP),
  }));

  const xOf = new Map(ideaNodes.map((n) => [n.id, n.x]));
  const gapNode = (g: Gap, x: number, y: number, loose_: boolean): Positioned => ({
    kind: "gap" as const,
    id: g.id,
    title: g.title || g.short || g.id,
    sub: g.canonicalDescription || g.description || "",
    severity: g.severity,
    loose: loose_,
    x,
    y,
  });

  const gapNodes: Positioned[] = connected.map((g) => {
    const xs = ideas.filter((i) => i.addresses.includes(g.id)).map((i) => xOf.get(i.id) ?? 0);
    return gapNode(g, xs.reduce((a, b) => a + b, 0) / xs.length, gapY, false);
  });
  // Two gaps addressed by the same idea would land on top of each other.
  gapNodes.sort((a, b) => a.x - b.x);
  for (let i = 1; i < gapNodes.length; i++) {
    const min = gapNodes[i - 1].x + step;
    if (gapNodes[i].x < min) gapNodes[i].x = min;
  }

  // Centre each band row over the graph. Left-aligned, a short last row hung off
  // to one side and the whole band read as a list that had been cut off rather
  // than as the pool the connected gaps were drawn from.
  const bodyWidth = Math.max(
    treeWidth,
    connected.length ? connected.length * step : 0,
    Math.min(loose.length, perRow) * step,
  );
  const looseNodes = loose.map((g, i) => {
    const row = Math.floor(i / perRow);
    const inRow = Math.min(perRow, loose.length - row * perRow);
    const rowWidth = inRow * step - COL_GAP;
    const left = PAD + Math.max(0, (bodyWidth - COL_GAP - rowWidth) / 2);
    return gapNode(g, left + (i % perRow) * step, PAD + row * LOOSE_H, true);
  });

  const nodes = [...looseNodes, ...gapNodes, ...ideaNodes];
  const links: Link[] = [];
  const have = new Set(nodes.map((n) => key(n.kind, n.id)));

  // A gap an ancestor already addresses is not information about the child: the
  // claim reaches it through the derivation edge, and drawing it again makes every
  // descendant a duplicate of its parent's fan. Only the gaps a child adds are its
  // own. (Records written by the current agent already store only the additions;
  // this also keeps older ones reading correctly.)
  const inheritedGaps = (id: string): Set<string> => {
    const out = new Set<string>();
    let cur = parentOf.get(id);
    const guard = new Set<string>();
    while (cur && !guard.has(cur)) {
      guard.add(cur);
      (ideaById.get(cur)?.addresses ?? []).forEach((g) => out.add(g));
      cur = parentOf.get(cur);
    }
    return out;
  };

  for (const idea of ideas) {
    const inherited = inheritedGaps(idea.id);
    for (const gapId of idea.addresses) {
      if (have.has(key("gap", gapId)) && !inherited.has(gapId)) {
        links.push({ from: key("idea", idea.id), to: key("gap", gapId), type: "addresses", rationale: "" });
      }
    }
    for (const rel of idea.relations) {
      if (have.has(key("idea", rel.target))) {
        links.push({
          from: key("idea", idea.id), to: key("idea", rel.target),
          type: rel.type, rationale: rel.rationale,
        });
      }
    }
  }

  const width = PAD + Math.max(step, ...nodes.map((n) => n.x + NODE_W));
  const height = ideaY + (maxDepth + 1) * NODE_H + maxDepth * ROW_GAP + PAD;
  return { nodes, links, width: Math.max(width, 320), height, bandHeight };
}

const EDGE_STYLE: Record<Link["type"], { color: string; dash?: string }> = {
  addresses: { color: "#B8B5AC" },
  "child-of": { color: "#5B5BF6" },
  "variant-of": { color: "#8B5CF6", dash: "5 4" },
  "merged-into": { color: "#5B9C75" },
  "inspired-by": { color: "#D9A04C", dash: "2 4" },
};

export function IdeaGraph({
  gaps, ideas, selectedId, marks, onOpen, onToggleMark, compact, chrome,
}: {
  gaps: Gap[];
  ideas: Idea[];
  selectedId?: string | null;
  // Session surfaces pass a `${kind}:${id}` → mark map; the page passes nothing.
  // `staged` doubles as "selected" — a node already loaded carries that mark too
  // once it's (re)staged, which is what tells the agent a turn is about it.
  marks?: Map<string, Mark>;
  onOpen?: (kind: GraphNodeKind, id: string) => void;
  // Present only on the session panel: clicking a node toggles it in/out of the
  // staged (= selected) set, whether or not it's already loaded.
  onToggleMark?: (kind: GraphNodeKind, id: string) => void;
  compact?: boolean;
  // In a session the graph is a *window*, and a window has one header row. The
  // host hands in that row and the graph puts its zoom controls into it, rather
  // than stacking a toolbar of its own underneath someone else's title bar.
  chrome?: (controls: ReactNode) => ReactNode;
}) {
  // 0 until the surface has actually been measured, so the first fit can wait for
  // a real number instead of fitting to the 900px placeholder and staying there.
  const [viewWidth, setViewWidth] = useState(0);
  const { nodes, links, width, height, bandHeight } =
    useMemo(() => layout(gaps, ideas, viewWidth || 900), [gaps, ideas, viewWidth]);
  // Zoom and pan are **one** state, not two. Anchoring a zoom to a point means
  // deriving the pan from the zoom that produced it; holding them apart meant
  // calling setPan from inside setZoom's updater — a side effect in a place React
  // is free to run twice, which it does in development, doubling every shift.
  const [view, setView] = useState<View>({ zoom: 1, x: 0, y: 0 });
  const { zoom } = view;
  const pan = view;
  const [hover, setHover] = useState<string | null>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<{ x: number; y: number; px: number; py: number } | null>(null);
  // How far the pointer travelled since it went down. A pan that starts on a node
  // must not also count as a click on it, and a click must not need a perfectly
  // still hand — so the two are told apart by distance, not by where they began.
  const movedRef = useRef(0);

  // Re-wrap the loose-gap band when the surface resizes (the splitter drags it
  // constantly in a session). Measured after every render rather than through a
  // ResizeObserver: every drag that changes this width re-renders the graph
  // anyway, and the observer does not fire at all in some of the shells this
  // runs in, which left the band wrapped for the old width.
  useLayoutEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const w = Math.round(el.clientWidth) || 900;
    // Guarded so the state write cannot loop: whole pixels only, and a change has
    // to be worth more than the noise of a re-layout before it re-renders.
    setViewWidth((cur) => (Math.abs(cur - w) > 2 ? w : cur));
  });
  useEffect(() => {
    const onResize = () => {
      const el = wrapRef.current;
      if (el) setViewWidth(el.clientWidth || 900);
    };
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  // Fit the graph to the surface once per *content* change. A panel is small
  // enough that the default 1:1 would show one node and nothing else — but the
  // fit must not re-run just because the surface resized. Re-wrapping the band
  // changes the extent, so keying the fit on the extent meant every frame of a
  // splitter drag, a panel-divider drag or a window reorder threw away the pan
  // and zoom the user had set: the window appeared to restart under the cursor.
  const contentKey = `${compact ? "c" : "f"}:${gaps.length}:${ideas.length}`;
  const fittedFor = useRef<string | null>(null);
  useLayoutEffect(() => {
    const el = wrapRef.current;
    if (!el || !viewWidth) return;          // wait for the first real measurement
    if (fittedFor.current === contentKey) return;
    fittedFor.current = contentKey;
    setView(fitView(el.clientWidth || width, el.clientHeight || height,
                    width, height, compact));
  });

  useEffect(() => {
    const up = () => { dragRef.current = null; };
    window.addEventListener("mouseup", up);
    return () => window.removeEventListener("mouseup", up);
  }, []);

  // Wheel = zoom, and the page must not scroll behind it. React attaches wheel
  // handlers passively, where `preventDefault` is ignored — so once the graph hit
  // its zoom limit the scroll leaked through and ran away with the page. A native
  // non-passive listener is the only way to hold it.
  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      // Zoom about the pointer: the point under the cursor must stay under it, so
      // the pan shifts by however much that point moved. Zooming about the origin
      // instead sends whatever you were looking at off the edge, and you spend the
      // zoom re-finding it.
      const box = el.getBoundingClientRect();
      setView((v) => zoomAbout(v, Math.exp(-e.deltaY * 0.0015),
                               e.clientX - box.left, e.clientY - box.top));
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, []);

  const nodeAt = new Map(nodes.map((n) => [key(n.kind, n.id), n]));
  const anchor = (k: string, side: "top" | "bottom") => {
    const n = nodeAt.get(k)!;
    return { x: n.x + NODE_W / 2, y: side === "top" ? n.y : n.y + NODE_H };
  };

  // The buttons zoom about the centre of the view — the same rule the wheel
  // follows about the cursor, so neither one moves what you are looking at.
  const zoomBy = (factor: number) => {
    const el = wrapRef.current;
    setView((v) => zoomAbout(v, factor,
                             (el?.clientWidth ?? 0) / 2, (el?.clientHeight ?? 0) / 2));
  };

  const controls = (
    <div className="graph-controls">
      <button className="graph-zoom" onClick={() => zoomBy(1.25)} title="Zoom in">+</button>
      <button className="graph-zoom" onClick={() => zoomBy(1 / 1.25)} title="Zoom out">−</button>
      <button className="graph-zoom graph-zoom--wide"
        onClick={() => {
          const el = wrapRef.current;
          if (!el) return;
          setView(fitView(el.clientWidth, el.clientHeight, width, height, compact));
        }}
        title="Fit to view"
      >fit</button>
      {!compact && (
        <div className="graph-legend">
          <LegendSwatch color="#B8B5AC" label="addresses" />
          <LegendSwatch color="#5B5BF6" label="child" />
          <LegendSwatch color="#8B5CF6" label="variant" dash />
          <LegendSwatch color="#5B9C75" label="merge" />
        </div>
      )}
    </div>
  );

  const body = !nodes.length ? (
    <div className="graph-empty">
      No gaps or ideas yet — the graph draws itself as the knowledge base grows.
    </div>
  ) : (
    <>
      <div
        className="graph-canvas"
        ref={wrapRef}
        onMouseDown={(e) => {
          // Panning starts anywhere, nodes included: grabbing the canvas at the
          // one spot that happens to hold a node and having nothing move is the
          // gesture failing for a reason the user cannot see.
          movedRef.current = 0;
          dragRef.current = { x: e.clientX, y: e.clientY, px: view.x, py: view.y };
        }}
        onMouseMove={(e) => {
          const d = dragRef.current;
          if (!d) return;
          const dx = e.clientX - d.x;
          const dy = e.clientY - d.y;
          movedRef.current = Math.max(movedRef.current, Math.abs(dx) + Math.abs(dy));
          setView((v) => ({ ...v, x: d.px + dx, y: d.py + dy }));
        }}
      >
        <div className="graph-stage"
          style={{ transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})` }}>
          <svg width={width} height={height} className="graph-svg">
            {bandHeight > 0 && (
              <>
                <line x1={PAD} y1={PAD + bandHeight - 9} x2={width - PAD} y2={PAD + bandHeight - 9}
                  stroke="#E5E3DD" strokeWidth="1" strokeDasharray="3 4" />
                <text x={PAD} y={PAD + bandHeight - 14} className="graph-band-label">
                  gaps no idea attacks yet
                </text>
              </>
            )}
            {links.map((l, i) => {
              const a = anchor(l.from, "top");
              const b = anchor(l.to, "bottom");
              const style = EDGE_STYLE[l.type];
              const lit = hover === l.from || hover === l.to;
              const mid = (a.y + b.y) / 2;
              return (
                <path
                  key={i}
                  d={`M ${a.x} ${a.y} C ${a.x} ${mid}, ${b.x} ${mid}, ${b.x} ${b.y}`}
                  fill="none"
                  stroke={style.color}
                  strokeWidth={lit ? 2 : 1.2}
                  strokeDasharray={style.dash}
                  opacity={lit ? 1 : 0.55}
                >
                  {l.rationale && <title>{`${l.type}: ${l.rationale}`}</title>}
                </path>
              );
            })}
          </svg>

          {nodes.map((n) => {
            const k = key(n.kind, n.id);
            const mark = marks?.get(k) ?? "none";
            return (
              <div
                key={k}
                className={
                  "graph-node graph-node--" + n.kind +
                  (n.loose ? " graph-node--loose" : "") +
                  (n.id === selectedId ? " graph-node--selected" : "") +
                  (mark !== "none" ? ` graph-node--${mark}` : "") +
                  (n.state === "provisional" ? " graph-node--provisional" : "")
                }
                style={{ left: n.x, top: n.y, width: NODE_W, height: NODE_H }}
                onMouseEnter={() => setHover(k)}
                onMouseLeave={() => setHover(null)}
                onClick={() => {
                  if (movedRef.current > DRAG_SLOP) return;   // that was a pan
                  if (onToggleMark) onToggleMark(n.kind, n.id);
                  else onOpen?.(n.kind, n.id);
                }}
                onDoubleClick={() => onOpen?.(n.kind, n.id)}
                title={n.sub}
                // Deliberately **not** an HTML5 drag source. A draggable node hands
                // the browser a translucent copy of itself the moment you press and
                // move, which is what you get when you meant to pan — and panning
                // starts on a node as often as not. Clicking already stages it, and
                // the nav tree is still draggable for the drag-into-chat gesture.
                onDragStart={(e) => e.preventDefault()}
              >
                <div className="graph-node-head">
                  {n.kind === "gap"
                    ? <span className="sev-dot" style={{ background: sevColor(n.severity) }} />
                    : <span className={"graph-state graph-state--" + (n.state || "provisional")} />}
                  <span className="graph-node-kind">{n.kind === "gap" ? "gap" : "idea"}</span>
                  {mark !== "none" && (
                    <span className={"graph-mark graph-mark--" + mark}>
                      {mark === "loaded" ? "in context" : "selected"}
                    </span>
                  )}
                </div>
                <div className="graph-node-title">{n.title}</div>
              </div>
            );
          })}
        </div>
      </div>
      {onToggleMark && (
        <div className="graph-hint">
          Click a node to select it for the next turn — even one already in context ·
          double-click to open its page
        </div>
      )}
    </>
  );

  // As a window (in a session) the host owns the header row; as a page the graph
  // is its own frame and carries the controls itself.
  return chrome ? (
    <div className="panel graph graph--compact">
      {chrome(controls)}
      {body}
    </div>
  ) : (
    <div className="graph">
      <div className="graph-toolbar">{controls}</div>
      {body}
    </div>
  );
}

// Fit the graph to the surface, but never past the point where the node titles
// stop being readable — below that, panning a legible graph beats seeing all of
// an illegible one.
// A panel is a fraction of the page, so it gets a lower floor — the point there
// is seeing the *shape* of the graph and which nodes are lit, and the page is one
// click away when the titles matter.
interface View { zoom: number; x: number; y: number }

const MIN_ZOOM = 0.15;
const MAX_ZOOM = 2.5;

/** Zoom about a point in canvas coordinates: whatever is under it stays under it.
 *  Zooming about the origin instead throws what you were looking at off the edge,
 *  and you spend the zoom finding it again. */
function zoomAbout(v: View, factor: number, px: number, py: number): View {
  const zoom = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, v.zoom * factor));
  const k = zoom / v.zoom;
  return { zoom, x: px - (px - v.x) * k, y: py - (py - v.y) * k };
}

const MIN_FIT = 0.55;
const MIN_FIT_COMPACT = 0.32;

/** Fit *and centre*: the zoom that shows the graph, plus the pan that puts it in
 *  the middle of the canvas. A graph narrower than its surface pinned to the left
 *  edge reads as cut off rather than as small, and the loose-gap band above it is
 *  the widest row, so left-alignment threw the whole picture off centre. */
function fitView(vw: number, vh: number, w: number, h: number, compact?: boolean): View {
  const raw = Math.min(1, vw / w, vh / h);
  const zoom = Number.isFinite(raw) ? Math.max(compact ? MIN_FIT_COMPACT : MIN_FIT, raw) : 1;
  return {
    zoom,
    // Only centre on an axis with room to spare; a graph larger than the surface
    // starts at its top-left, where reading starts.
    x: Math.max(0, (vw - w * zoom) / 2),
    y: Math.max(0, (vh - h * zoom) / 2),
  };
}

function sevColor(sev?: string) {
  return sev === "high" ? "#C75B5B" : sev === "low" ? "#7B9C6B" : "#D9A04C";
}

function LegendSwatch({ color, label, dash }: { color: string; label: string; dash?: boolean }) {
  return (
    <span className="graph-legend-item">
      <svg width="18" height="6" aria-hidden>
        <line x1="0" y1="3" x2="18" y2="3" stroke={color} strokeWidth="1.6"
          strokeDasharray={dash ? "4 3" : undefined} />
      </svg>
      {label}
    </span>
  );
}
