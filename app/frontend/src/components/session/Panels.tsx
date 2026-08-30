// The session's right-hand column: a stack of windows the user adds, removes and
// resizes. The chat is deliberately *not* one of them — it is always there, on the
// left, because it is the session; these are views onto the workspace beside it.
//
// One window is one white block with **one** header row: its name, whatever
// controls that kind needs, and the close button. Anything a window would have put
// in a second toolbar row goes into that same row instead. *Which* windows are
// open is not a per-window decision — it is the Windows menu in the session bar.
//
// The kinds are the two representations of one selection the vault asks for — the
// graph with loaded items highlighted **in place**, and the Loaded Context list
// which "is the single source of truth for what the model can see" — plus two
// working surfaces: an idea page, and the tree of thoughts of the session's
// current run.
import { Fragment, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { Icon } from "../Icon";
import { IdeaGraph } from "../IdeaGraph";
import type { Mark } from "../IdeaGraph";
import { StepArgs, agentLabel, useToolSpecs } from "../PlanReview";
import { NodeInspector, ThoughtTree, TruncationNote } from "../ThoughtTree";
import { isOrchestratorRun, progressForRun } from "../../lib/orchestratorProgress";
import { IdeaContent } from "../views/IdeaContent";
import { useFullIdea } from "../views/Ideas";
import { treeForRun } from "../../lib/thoughts";
import type {
  AgentPlan, Column, ContextRef, Gap, Idea, Panel, PanelKind, PlanStatus, RunState,
  ThoughtNode,
} from "../../lib/types";

/** A planned turn as the transcript recorded it — what the plan window falls back to
 *  when the run that produced it is no longer streaming (a reloaded session, or a run
 *  the backend's index has since pruned). */
export interface PlannedTurn {
  plan: AgentPlan;
  status: PlanStatus;
}

export const KIND_LABEL: Record<PanelKind, string> = {
  chat: "Chat",
  graph: "Gap & idea graph",
  context: "Loaded context",
  plan: "Agent plan",
  idea: "Idea",
  thoughts: "Tree of thoughts",
};

/** Where a dragged window would land. Columns are addressed by index; `newCol`
 *  means "open a fresh column here" rather than "join the one at this index". */
type Drop =
  | { newCol: false; col: number; at: number }
  | { newCol: true; at: number };

const sameDrop = (a: Drop | null, b: Drop | null) =>
  a === b || (!!a && !!b && a.newCol === b.newCol && a.at === b.at
    && (a.newCol || b.newCol || a.col === b.col));

export function PanelGrid({
  columns, onChange, gaps, ideas, runs, planned, marks, loaded,
  onToggleMark, onOpenNode, onUnload, onOpenIdea, renderChat,
}: {
  columns: Column[];
  onChange: (columns: Column[]) => void;
  gaps: Gap[];
  ideas: Idea[];
  runs: RunState[];
  planned: PlannedTurn[];
  marks: Map<string, Mark>;
  loaded: ContextRef[];
  onToggleMark: (kind: "gap" | "idea", id: string) => void;
  onOpenNode: (kind: "gap" | "idea", id: string) => void;
  onUnload: (ref: ContextRef) => void;
  onOpenIdea: (id: string) => void;
  /** The chat is a window in the grid, but only its owner can build it. */
  renderChat: (grip: ReactNode) => ReactNode;
}) {
  const gridRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<{ y: number; a: number; b: number } | null>(null);
  const [dragging, setDragging] = useState<string | null>(null);
  const [drop, setDrop] = useState<Drop | null>(null);
  // A drag reads and writes the layout many times between renders, so it works
  // off the latest props rather than the ones its mousedown closed over.
  const latest = useRef({ columns, onChange });
  useEffect(() => { latest.current = { columns, onChange }; });

  const setPanel = (uid: string, patch: Partial<Panel>) =>
    onChange(columns.map((c) => ({
      ...c, panels: c.panels.map((p) => (p.uid === uid ? { ...p, ...patch } : p)),
    })));

  const remove = (uid: string) =>
    onChange(prune(columns.map((c) => ({ ...c, panels: c.panels.filter((p) => p.uid !== uid) }))));

  /** Drop any column that lost its last window. */
  const prune = (cols: Column[]) => cols.filter((c) => c.panels.length > 0);

  // --- moving a window ---------------------------------------------------------

  /** Which slot the pointer is over, and which quarter of it — the edges open a
   *  new column, the middle stacks above or below. */
  const hitTest = (x: number, y: number): Drop | null => {
    const grid = gridRef.current;
    if (!grid) return null;
    const cols = latest.current.columns;
    const slots = Array.from(grid.querySelectorAll<HTMLElement>(".panel-slot"));
    for (const slot of slots) {
      const r = slot.getBoundingClientRect();
      if (x < r.left || x > r.right || y < r.top || y > r.bottom) continue;
      const ci = Number(slot.dataset.col);
      const pi = Number(slot.dataset.idx);
      const relX = (x - r.left) / r.width;
      // A narrow window has no room for three zones side by side, so the edge
      // bands are capped in pixels as well as in fractions.
      const edge = Math.min(0.28, 90 / r.width);
      if (relX < edge) return { newCol: true, at: ci };
      if (relX > 1 - edge) return { newCol: true, at: ci + 1 };
      return { newCol: false, col: ci, at: (y - r.top) / r.height < 0.5 ? pi : pi + 1 };
    }
    // Past the ends of the row — drop into a new column there.
    const gr = grid.getBoundingClientRect();
    if (y >= gr.top && y <= gr.bottom) {
      if (x < gr.left + 40) return { newCol: true, at: 0 };
      if (x > gr.right - 40) return { newCol: true, at: cols.length };
    }
    return null;
  };

  /** Apply a drop: pull the window out of wherever it was, then put it back. */
  const applyDrop = (uid: string, target: Drop) => {
    const cols = latest.current.columns;
    let from = { col: -1, idx: -1 };
    cols.forEach((c, ci) => c.panels.forEach((p, pi) => {
      if (p.uid === uid) from = { col: ci, idx: pi };
    }));
    if (from.col < 0) return;
    const panel = cols[from.col].panels[from.idx];

    // A no-op drop (back where it came from, or the slot just after itself)
    // must not churn the layout.
    if (!target.newCol && target.col === from.col
      && (target.at === from.idx || target.at === from.idx + 1)) return;

    const next = cols.map((c) => ({ ...c, panels: [...c.panels] }));
    next[from.col].panels.splice(from.idx, 1);

    if (target.newCol) {
      // Removing the window may have emptied its column, which shifts every
      // index to the right of it — including where this new column belongs.
      const emptied = next[from.col].panels.length === 0;
      const at = emptied && target.at > from.col ? target.at - 1 : target.at;
      const pruned = prune(next);
      pruned.splice(at, 0, {
        uid: `c${Date.now()}`,
        weight: 1,
        panels: [{ ...panel, weight: 1 }],
      });
      onChange(pruned);
      return;
    }

    const col = next[target.col];
    // Within the same column, taking the window out shifts everything below it.
    const at = target.col === from.col && target.at > from.idx ? target.at - 1 : target.at;
    col.panels.splice(at, 0, panel);
    onChange(prune(next));
  };

  const onGripDown = (uid: string) => (e: React.MouseEvent) => {
    e.preventDefault();
    setDragging(uid);
    setDrop(null);
    document.body.classList.add("is-panel-dragging");
    let target: Drop | null = null;

    const move = (ev: MouseEvent) => {
      const next = hitTest(ev.clientX, ev.clientY);
      if (sameDrop(next, target)) return;
      target = next;
      setDrop(next);
    };
    const up = () => {
      document.body.classList.remove("is-panel-dragging");
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", up);
      setDragging(null);
      setDrop(null);
      if (target) applyDrop(uid, target);
    };
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
  };

  // --- resizing ----------------------------------------------------------------

  // Dragging a divider moves weight between the two windows it separates, so the
  // rest of the column stays where it is.
  //
  // The conversion from pixels to weight is *measured*, not guessed: the slots
  // are flex-basis 0, so their rendered heights and their weights are the same
  // ratio, and `(hA + hB) / (a + b)` is exactly how many pixels one unit of
  // weight is worth right now.
  const onDividerDown = (ci: number, index: number) => (e: React.MouseEvent) => {
    e.preventDefault();
    const slotA = (e.currentTarget as HTMLElement).parentElement;
    const slotB = slotA?.nextElementSibling as HTMLElement | null;
    const panels = columns[ci].panels;
    const a = panels[index].weight;
    const b = panels[index + 1].weight;
    const span = (slotA?.offsetHeight || 0) + (slotB?.offsetHeight || 0);
    const pxPerWeight = span > 0 ? span / (a + b) : 200;
    dragRef.current = { y: e.clientY, a, b };
    const move = (ev: MouseEvent) => {
      const d = dragRef.current;
      if (!d) return;
      const total = d.a + d.b;
      const shift = (ev.clientY - d.y) / pxPerWeight;
      const at = Math.max(0.18, Math.min(total - 0.18, d.a + shift));
      const cur = latest.current.columns;
      latest.current.onChange(cur.map((c, i) => (i !== ci ? c : {
        ...c,
        panels: c.panels.map((p, j) =>
          j === index ? { ...p, weight: at }
            : j === index + 1 ? { ...p, weight: total - at } : p),
      })));
    };
    const up = () => {
      dragRef.current = null;
      document.body.classList.remove("is-row-resizing");
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", up);
    };
    // While a divider is being dragged the cursor is over window *content*, so
    // without this the drag selects text as it passes.
    document.body.classList.add("is-row-resizing");
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
  };

  /** The same trade, horizontally, between two columns. */
  const onSplitterDown = (index: number) => (e: React.MouseEvent) => {
    e.preventDefault();
    const splitter = e.currentTarget as HTMLElement;
    const colA = splitter.previousElementSibling as HTMLElement | null;
    const colB = splitter.nextElementSibling as HTMLElement | null;
    const a = columns[index].weight;
    const b = columns[index + 1].weight;
    const span = (colA?.offsetWidth || 0) + (colB?.offsetWidth || 0);
    const pxPerWeight = span > 0 ? span / (a + b) : 400;
    const x0 = e.clientX;
    const move = (ev: MouseEvent) => {
      const total = a + b;
      const shift = (ev.clientX - x0) / pxPerWeight;
      const at = Math.max(0.22, Math.min(total - 0.22, a + shift));
      const cur = latest.current.columns;
      latest.current.onChange(cur.map((c, i) =>
        i === index ? { ...c, weight: at }
          : i === index + 1 ? { ...c, weight: total - at } : c));
    };
    const up = () => {
      document.body.classList.remove("is-col-resizing");
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", up);
    };
    document.body.classList.add("is-col-resizing");
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
  };

  // --- render ------------------------------------------------------------------

  const grip = (uid: string) => (
    <span
      className="panel-grip"
      onMouseDown={onGripDown(uid)}
      title="Drag to move — edges of a window open a new column"
      aria-label="Move window"
    >
      <Icon name="grip" />
    </span>
  );

  if (!columns.length) {
    return (
      <div className="session-grid" ref={gridRef}>
        <div className="panel-empty">
          <p>No windows open.</p>
          <p className="panel-empty-hint">Pick one from <b>Windows</b> in the bar above.</p>
        </div>
      </div>
    );
  }

  return (
    <div className="session-grid" ref={gridRef}>
      {columns.map((col, ci) => (
        <Fragment key={col.uid}>
          {ci > 0 && (
            <div className="session-splitter" onMouseDown={onSplitterDown(ci - 1)}
              title="Drag to resize"><span /></div>
          )}
          <div className="session-col" style={{ flexGrow: col.weight }}>
            {col.panels.map((panel, pi) => (
              <div
                className={"panel-slot" + (dragging === panel.uid ? " panel-slot--dragging" : "")}
                key={panel.uid}
                data-col={ci}
                data-idx={pi}
                style={{ flexGrow: panel.weight }}
              >
                {drop && !drop.newCol && drop.col === ci && drop.at === pi && (
                  <div className="drop-line drop-line--top" />
                )}
                {drop && !drop.newCol && drop.col === ci && drop.at === pi + 1
                  && pi === col.panels.length - 1 && <div className="drop-line drop-line--bottom" />}
                {drop?.newCol && drop.at === ci && pi === 0 && <div className="drop-line drop-line--left" />}
                {drop?.newCol && drop.at === ci + 1 && pi === 0
                  && ci === columns.length - 1 && <div className="drop-line drop-line--right" />}

                {panel.kind === "chat" ? (
                  renderChat(grip(panel.uid))
                ) : (
                  <PanelBody
                    panel={panel} gaps={gaps} ideas={ideas} runs={runs} planned={planned}
                    marks={marks}
                    loaded={loaded} onToggleMark={onToggleMark} onOpenNode={onOpenNode}
                    onUnload={onUnload}
                    head={(controls) => (
                      <div className="panel-head">
                        {grip(panel.uid)}
                        <span className="panel-kind">{KIND_LABEL[panel.kind]}</span>
                        {panel.kind === "idea" && (
                          <select className="panel-ref" value={panel.ref ?? ""}
                            onChange={(e) => setPanel(panel.uid, { ref: e.target.value || null })}>
                            <option value="">pick an idea…</option>
                            {ideas.map((idea) => (
                              <option key={idea.id} value={idea.id}>{idea.short}</option>
                            ))}
                          </select>
                        )}
                        {controls}
                        <div className="panel-head-right">
                          {panel.kind === "idea" && panel.ref && (
                            <button className="ghost-btn" title="Open its page"
                              onClick={() => onOpenIdea(panel.ref!)}><Icon name="arrow-up-right" /></button>
                          )}
                          <button className="ghost-btn" title="Close window"
                            onClick={() => remove(panel.uid)}><Icon name="x" /></button>
                        </div>
                      </div>
                    )}
                  />
                )}

                {pi < col.panels.length - 1 && (
                  <div className="panel-divider" onMouseDown={onDividerDown(ci, pi)}
                    title="Drag to resize"><span /></div>
                )}
              </div>
            ))}
          </div>
        </Fragment>
      ))}
    </div>
  );
}

/** One window. `head` is a render prop so each kind can put its own controls into
 *  the *single* header row rather than growing a toolbar of its own underneath. */
function PanelBody({
  panel, gaps, ideas, runs, planned, marks, loaded, head, onToggleMark, onOpenNode,
  onUnload,
}: {
  panel: Panel; gaps: Gap[]; ideas: Idea[]; runs: RunState[]; planned: PlannedTurn[];
  marks: Map<string, Mark>; loaded: ContextRef[];
  head: (controls: ReactNode) => ReactNode;
  onToggleMark: (kind: "gap" | "idea", id: string) => void;
  onOpenNode: (kind: "gap" | "idea", id: string) => void;
  onUnload: (ref: ContextRef) => void;
}) {
  if (panel.kind === "graph") {
    return (
      <IdeaGraph
        compact gaps={gaps} ideas={ideas} marks={marks}
        onToggleMark={onToggleMark} onOpen={onOpenNode}
        chrome={head}
      />
    );
  }

  if (panel.kind === "plan") {
    return <PlanPanel runs={runs} planned={planned} head={head} />;
  }

  if (panel.kind === "thoughts") {
    return <ThoughtsPanel runs={runs} head={head} />;
  }

  if (panel.kind === "idea") {
    return (
      <Window head={head(null)}>
        {panel.ref
          ? <PinnedIdea id={panel.ref} />
          : <div className="empty-state empty-state--inline">Pick an idea to pin here.</div>}
      </Window>
    );
  }

  return (
    <Window head={head(null)}>
      <LoadedContext loaded={loaded} onUnload={onUnload} />
    </Window>
  );
}

export function Window({ head, children }: { head: ReactNode; children: ReactNode }) {
  return (
    <div className="panel">
      {head}
      <div className="panel-body">{children}</div>
    </div>
  );
}

/** The orchestrator's plan for the session's current turn, and where it has got to.
 *
 *  The tree-of-thoughts window says what one subagent is *considering*; this one says
 *  what the orchestrator decided to *do*: which agent each step calls, with which
 *  arguments, in which order, and which of them have run. While a plan is only
 *  proposed, nothing here has run yet — the same plan is in the chat with a Start
 *  button, which is where it is edited, because editing it is a decision and decisions
 *  belong on the transcript. */
function PlanPanel({
  runs, planned, head,
}: { runs: RunState[]; planned: PlannedTurn[]; head: (c: ReactNode) => ReactNode }) {
  const specs = useToolSpecs();
  // Long instructions are clamped: the window is read as a *sequence* — which agent,
  // with which parameters, where the run has got to — and one step's four-line prompt
  // pushes the rest of it off screen. The whole text is one click away.
  const [open, setOpen] = useState<number[]>([]);

  // The newest orchestrator run of the session: a turn that was planned and then
  // started is two runs, and the second is the one with something to show.
  const run = [...runs].reverse().find(isOrchestratorRun);
  // With no run to read, the transcript still has the plan — a session opened days
  // later has no event stream, and an empty window there would say the turn had no
  // plan rather than that nobody is watching it run.
  const recorded = planned[planned.length - 1];
  const progress = run ? progressForRun(run)
    : recorded ? {
      phase: recorded.status === "proposed" ? "Waiting for you to start it"
        : recorded.status === "approved" ? "Started" : "Not run",
      goal: recorded.plan.goal,
      criteria: recorded.plan.done_criteria || [],
      detail: recorded.status === "proposed"
        ? "Nothing has run yet — read the plan in the chat and press Start"
        : recorded.status === "approved" ? "This plan was started from the chat"
        : "You turned this plan down",
      awaitingApproval: recorded.status === "proposed",
      updatedAt: null,
      steps: recorded.plan.steps.map((step, index) => ({
        number: index + 1, tool: step.tool, args: step.args || {},
        needs: step.needs || [], why: step.why,
        title: String((step.args || {}).instruction || (step.args || {}).query
          || step.why || step.tool),
        status: "pending" as const, error: "",
      })),
    } : null;
  const live = run?.status === "running";
  const steps = progress?.steps ?? [];
  const settled = steps.filter((s) => s.status === "done" || s.status === "failed").length;

  // The header carries the count; the phase is the line above the steps, where it is
  // read together with what the run is doing rather than squeezed into the title row.
  const controls = steps.length ? (
    <span className="panel-note">
      {live && <span className="sidebar-spin" />}
      {settled}/{steps.length} steps
    </span>
  ) : null;

  return (
    <Window head={head(controls)}>
      {!progress ? (
        <div className="empty-state empty-state--inline">
          No planned turn in this session yet. Ask for something and the plan appears
          here before it runs.
        </div>
      ) : (
        <div className="plan-panel">
          <div className="plan-panel-top">
            <span className={"plan-phase plan-phase--"
              + (progress.awaitingApproval ? "waiting" : live ? "live" : "settled")}>
              {progress.phase}
            </span>
            {progress.goal && <p className="plan-panel-goal">{progress.goal}</p>}
            {steps.length > 1 && (
              <div className="plan-track" role="presentation">
                {steps.map((step) => (
                  <span className={"plan-track-seg plan-track-seg--" + step.status}
                    key={step.number} />
                ))}
              </div>
            )}
          </div>

          {steps.length === 0 ? (
            <div className="empty-state empty-state--inline">
              {live ? "Planning…" : "This turn needed no agents — it was answered directly."}
            </div>
          ) : (
            <ol className="plan-steps plan-steps--rail">
              {steps.map((step) => {
                const expanded = open.includes(step.number);
                const long = step.title.length > 150;
                return (
                  <li className={"plan-step plan-step--" + step.status} key={step.number}>
                    <span className="plan-step-num" aria-label={step.status}>
                      {step.status === "active" ? <span className="sidebar-spin" />
                        : step.status === "done" ? "✓"
                        : step.status === "failed" ? "!" : step.number}
                    </span>
                    <div className="plan-step-body">
                      <div className="plan-step-head">
                        <span className="plan-step-agent">{agentLabel(step.tool)}</span>
                        <span className={"plan-state plan-state--" + step.status}>
                          {step.status === "active" ? "running" : step.status}
                        </span>
                      </div>
                      <div className={"plan-step-title"
                        + (long && !expanded ? " plan-step-title--clamped" : "")}>
                        {step.title}
                      </div>
                      {long && (
                        <button className="plan-step-more"
                          onClick={() => setOpen((cur) => expanded
                            ? cur.filter((n) => n !== step.number) : [...cur, step.number])}>
                          {expanded ? "less" : "more"}
                        </button>
                      )}
                      <StepArgs step={{ tool: step.tool, args: step.args, why: step.why,
                        needs: step.needs }} specs={specs} />
                      {step.error && <div className="plan-step-error">{step.error}</div>}
                    </div>
                  </li>
                );
              })}
            </ol>
          )}

          {progress.criteria.length > 0 && (
            <div className="plan-criteria">
              <span className="plan-criteria-label">Done when</span>
              <ul>{progress.criteria.map((c, i) => <li key={i}>{c}</li>)}</ul>
            </div>
          )}

          <div className="plan-panel-now">
            <span className="plan-panel-now-label">Now</span>
            <span>{progress.detail}</span>
          </div>
        </div>
      )}
    </Window>
  );
}

/** The tree of thoughts of the session's current run — live while it thinks.
 *
 *  Not a stage list: the stages say *where* the agent is, the tree says *what it
 *  is considering*, and the second is the thing worth a window beside the chat.
 *  It streams (`tools/trace.py` emits every node as it is minted) and settles onto
 *  the run entry the envelope carries once the run ends. */
function ThoughtsPanel({ runs, head }: { runs: RunState[]; head: (c: ReactNode) => ReactNode }) {
  const [showPruned, setShowPruned] = useState(true);
  const [selected, setSelected] = useState<ThoughtNode | null>(null);
  const run = runs[runs.length - 1];
  const tree = treeForRun(run);
  const live = run?.status === "running";

  const controls = tree ? (
    <>
      <label className="panel-check" title="Losing branches are recorded, never dropped">
        <input type="checkbox" checked={showPruned}
          onChange={(e) => setShowPruned(e.target.checked)} />
        pruned
      </label>
      <span className="panel-note">
        {live && <span className="sidebar-spin" />}
        {tree.nodes.length} thought{tree.nodes.length === 1 ? "" : "s"}
      </span>
    </>
  ) : null;

  return (
    <Window head={head(controls)}>
      {!tree ? (
        <div className="empty-state empty-state--inline">
          {live
            ? "Waiting for the first branch…"
            : "No run in this session yet. Send a turn and its tree grows here."}
        </div>
      ) : (
        <div className="thoughts-panel">
          <ThoughtTree run={tree} showPruned={showPruned} fit
            selected={selected} onSelect={setSelected} />
          <TruncationNote run={tree} />
          {tree.outcome.escalation && (
            <div className="run-band-escalation">
              <b>Escalated</b> {tree.outcome.escalation}
              {tree.outcome.escalationDiagnosis && ` — ${tree.outcome.escalationDiagnosis}`}
            </div>
          )}
          {selected && <NodeInspector node={selected} onClose={() => setSelected(null)} />}
        </div>
      )}
    </Window>
  );
}

function PinnedIdea({ id }: { id: string }) {
  const idea = useFullIdea(id);
  if (!idea) return <div className="empty-state empty-state--inline">Loading…</div>;
  return (
    <div className="panel-idea">
      <div className="panel-idea-head">
        <span className="panel-idea-title">{idea.title}</span>
      </div>
      <IdeaContent idea={idea} />
    </div>
  );
}

/** The Loaded Context window — the single source of truth for what the model can
 *  see. Entries are references: an id, a label and a digest. */
function LoadedContext({
  loaded, onUnload,
}: { loaded: ContextRef[]; onUnload: (ref: ContextRef) => void }) {
  if (!loaded.length) {
    return (
      <div className="empty-state empty-state--inline">
        Nothing loaded yet. Drop something into the chat, click a node in the graph,
        or use <b>+</b> in the composer.
      </div>
    );
  }
  return (
    <div className="ctx-list">
      {loaded.map((c) => (
        <div className={"ctx-item ctx-item--" + c.kind} key={`${c.kind}:${c.id}`}>
          <div className="ctx-item-head">
            <span className={"ctx-kind ctx-kind--" + c.kind}>{c.kind}</span>
            <span className="ctx-item-label">{c.label}</span>
            <button className="ghost-btn" title="Remove from context"
              onClick={() => onUnload(c)}><Icon name="x" /></button>
          </div>
          {c.digest && <p className="ctx-item-digest">{c.digest}</p>}
          <div className="ctx-item-id">{c.id}</div>
        </div>
      ))}
    </div>
  );
}
