// The session's right-hand column: a stack of windows the user adds, removes and
// resizes. The chat is deliberately *not* one of them — it is always there, on the
// left, because it is the session; these are views onto the workspace beside it.
//
// One window is one white block with **one** header row: the kind picker, whatever
// controls that kind needs, and the close button. Anything a window would have put
// in a second toolbar row goes into that same row instead.
//
// The kinds are the two representations of one selection the vault asks for — the
// graph with loaded items highlighted **in place**, and the Loaded Context list
// which "is the single source of truth for what the model can see" — plus two
// working surfaces: an idea page, and the tree of thoughts of the session's
// current run.
import { useRef, useState } from "react";
import type { ReactNode } from "react";
import { Icon } from "../Icon";
import { IdeaGraph } from "../IdeaGraph";
import type { Mark } from "../IdeaGraph";
import { NodeInspector, ThoughtTree, TruncationNote } from "../ThoughtTree";
import { IdeaContent } from "../views/IdeaContent";
import { useFullIdea } from "../views/Ideas";
import { treeForRun } from "../../lib/thoughts";
import type {
  ContextRef, Gap, Idea, Panel, PanelKind, RunState, ThoughtNode,
} from "../../lib/types";

const KIND_LABEL: Record<PanelKind, string> = {
  graph: "Gap & idea graph",
  context: "Loaded context",
  idea: "Idea",
  thoughts: "Tree of thoughts",
};

export function PanelColumn({
  panels, onChange, gaps, ideas, runs, marks, loaded,
  onToggleMark, onOpenNode, onUnload, onOpenIdea,
}: {
  panels: Panel[];
  onChange: (panels: Panel[]) => void;
  gaps: Gap[];
  ideas: Idea[];
  runs: RunState[];
  marks: Map<string, Mark>;
  loaded: ContextRef[];
  onToggleMark: (kind: "gap" | "idea", id: string) => void;
  onOpenNode: (kind: "gap" | "idea", id: string) => void;
  onUnload: (ref: ContextRef) => void;
  onOpenIdea: (id: string) => void;
}) {
  const columnRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<{ index: number; y: number; a: number; b: number } | null>(null);

  const setPanel = (uid: string, patch: Partial<Panel>) =>
    onChange(panels.map((p) => (p.uid === uid ? { ...p, ...patch } : p)));

  const add = (kind: PanelKind) =>
    onChange([...panels, { uid: `p${Date.now()}${panels.length}`, kind, ref: null, weight: 1 }]);

  const remove = (uid: string) => onChange(panels.filter((p) => p.uid !== uid));

  // Dragging a divider moves weight between the two panels it separates, so the
  // rest of the column stays where it is.
  //
  // The conversion from pixels to weight is *measured*, not guessed: the two slots
  // are flex-basis 0, so their rendered heights and their weights are the same
  // ratio, and `(hA + hB) / (a + b)` is exactly how many pixels one unit of weight
  // is worth right now. Deriving it from the column height instead was wrong by
  // whatever the dividers and the add-window button take up — hence the fudge
  // factor that used to sit here, and hence the divider outrunning the cursor.
  const onDividerDown = (index: number) => (e: React.MouseEvent) => {
    e.preventDefault();
    const slotA = (e.currentTarget as HTMLElement).parentElement;
    const slotB = slotA?.nextElementSibling as HTMLElement | null;
    const a = panels[index].weight;
    const b = panels[index + 1].weight;
    const span = (slotA?.offsetHeight || 0) + (slotB?.offsetHeight || 0);
    // Fall back to the column only if the slots have not laid out yet.
    const pxPerWeight = span > 0
      ? span / (a + b)
      : (columnRef.current?.clientHeight || 600) / Math.max(1, panels.reduce((t, p) => t + p.weight, 0));
    dragRef.current = { index, y: e.clientY, a, b };
    const move = (ev: MouseEvent) => {
      const d = dragRef.current;
      if (!d) return;
      const total = d.a + d.b;
      const shift = (ev.clientY - d.y) / pxPerWeight;
      const next = Math.max(0.18, Math.min(total - 0.18, d.a + shift));
      onChange(panels.map((p, i) =>
        i === d.index ? { ...p, weight: next }
          : i === d.index + 1 ? { ...p, weight: total - next } : p));
    };
    const up = () => {
      dragRef.current = null;
      document.body.classList.remove("is-row-resizing");
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", up);
    };
    // While a divider is being dragged the cursor is over panel *content*, so
    // without this the drag selects text as it passes.
    document.body.classList.add("is-row-resizing");
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
  };

  return (
    <div className="panel-col" ref={columnRef}>
      {panels.length === 0 && (
        <div className="panel-empty">
          <p>No windows open.</p>
          <AddPanel onAdd={add} inline />
        </div>
      )}

      {panels.map((panel, i) => (
        <div className="panel-slot" key={panel.uid} style={{ flexGrow: panel.weight }}>
          <PanelBody
            panel={panel} gaps={gaps} ideas={ideas} runs={runs} marks={marks}
            loaded={loaded} onToggleMark={onToggleMark} onOpenNode={onOpenNode}
            onUnload={onUnload}
            head={(controls) => (
              <div className="panel-head">
                <select
                  className="panel-kind"
                  value={panel.kind}
                  onChange={(e) => setPanel(panel.uid, { kind: e.target.value as PanelKind, ref: null })}
                >
                  {(Object.keys(KIND_LABEL) as PanelKind[]).map((k) => (
                    <option key={k} value={k}>{KIND_LABEL[k]}</option>
                  ))}
                </select>
                {panel.kind === "idea" && (
                  <select className="panel-ref" value={panel.ref ?? ""}
                    onChange={(e) => setPanel(panel.uid, { ref: e.target.value || null })}>
                    <option value="">pick an idea…</option>
                    {ideas.map((idea) => <option key={idea.id} value={idea.id}>{idea.short}</option>)}
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

          {i < panels.length - 1 && (
            <div className="panel-divider" onMouseDown={onDividerDown(i)} title="Drag to resize">
              <span />
            </div>
          )}
        </div>
      ))}

      {panels.length > 0 && <AddPanel onAdd={add} />}
    </div>
  );
}

/** One window. `head` is a render prop so each kind can put its own controls into
 *  the *single* header row rather than growing a toolbar of its own underneath. */
function PanelBody({
  panel, gaps, ideas, runs, marks, loaded, head, onToggleMark, onOpenNode, onUnload,
}: {
  panel: Panel; gaps: Gap[]; ideas: Idea[]; runs: RunState[];
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
          <ThoughtTree run={tree} showPruned={showPruned}
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

function AddPanel({ onAdd, inline }: { onAdd: (k: PanelKind) => void; inline?: boolean }) {
  const [open, setOpen] = useState(false);
  return (
    <div className={"panel-add" + (inline ? " panel-add--inline" : "")}>
      <button className="panel-add-btn" onClick={() => setOpen(!open)}>
        <Icon name="plus" /> Add window
      </button>
      {open && (
        <div className="panel-add-menu" onMouseLeave={() => setOpen(false)}>
          {(Object.keys(KIND_LABEL) as PanelKind[]).map((k) => (
            <button key={k} onClick={() => { onAdd(k); setOpen(false); }}>{KIND_LABEL[k]}</button>
          ))}
        </div>
      )}
    </div>
  );
}
