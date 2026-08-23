// The session's conversation — always present, always on the left.
//
// Until the orchestrator exists, one turn *is* one creative-thinker call: the
// message becomes the run's `task`, the staged items become its context, and the
// composer exposes the thinker's own knobs (mode, width, depth, styles, tool use)
// rather than pretending a planner picked them. That is what the demo banner says
// out loud, and it is why those controls are here at all.
import { useEffect, useRef, useState } from "react";
import { Icon } from "../Icon";
import { LiveThoughts } from "../LiveThoughts";
import { ContextPicker } from "./ContextPicker";
import { EnvelopeResult } from "./EnvelopeResult";
import type { DraftActions } from "./EnvelopeResult";
import { readDrop } from "../../lib/dnd";
import type { ChatMessage, ContextRef, Idea, RunState, Session } from "../../lib/types";

// The style presets the thinker ships (src/agents/creative_thinker/styles/).
export const STYLES = [
  "neutral", "skeptic", "optimizer", "minimalist", "extremist", "transferer", "combiner",
];

export interface ComposerSettings {
  mode: "generation" | "refinement";
  ideaId: string | null;
  width: number;
  depth: number;
  styles: string[];
  toolUse: boolean;
}

export function Chat({
  session, staged, runs, ideas, settings, sending,
  onStage, onUnstage, onUploadFile, onSend, onSettings, onOpenIdea, onShowThoughts, drafts,
}: {
  session: Session | null;
  staged: ContextRef[];
  runs: RunState[];
  ideas: Idea[];
  settings: ComposerSettings;
  sending: boolean;
  onStage: (refs: ContextRef[]) => void;
  onUnstage: (ref: ContextRef) => void;
  onUploadFile: (file: File) => void;
  onSend: (text: string) => void;
  onSettings: (patch: Partial<ComposerSettings>) => void;
  onOpenIdea: (id: string) => void;
  onShowThoughts: () => void;
  drafts: DraftActions;
}) {
  const [text, setText] = useState("");
  const [picker, setPicker] = useState(false);
  const [dropping, setDropping] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);
  const messages = session?.messages ?? [];

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages.length, runs.length]);

  const submit = () => {
    if (sending || (!text.trim() && staged.length === 0)) return;
    onSend(text.trim());
    setText("");
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDropping(false);
    const { refs, files } = readDrop(e);
    if (refs.length) {
      onStage(refs.map((r) => ({ kind: r.kind, id: r.id, label: r.label, digest: "" })));
    }
    files.forEach(onUploadFile);
  };

  return (
    <section className="chat-pane"
      onDragOver={(e) => { e.preventDefault(); setDropping(true); }}
      onDragLeave={(e) => { if (e.currentTarget === e.target) setDropping(false); }}
      onDrop={handleDrop}
    >
      <div className="panel-head chat-head">
        <span className="panel-kind panel-kind--static">Chat</span>
        {/* One chip instead of a paragraph. The full explanation is a tooltip —
            it matters once, and after that it is a banner you read past. */}
        <span className="demo-chip"
          title="The orchestrator is not built yet, so this chat calls the creative thinker directly: your message is the run's task and the loaded context is its input. Its own knobs are in the composer.">
          <Icon name="flask" size={13} /> creative thinker demo
        </span>
      </div>

      <div className="chat-scroll">
        {messages.length === 0 && !runs.length && (
          <div className="chat-empty">
            <div className="chat-empty-title">Nothing loaded yet</div>
            <p>
              Drag a gap, paper or idea in from the left — or from the graph — and ask for
              something. The selection stays visible and editable for the whole session.
            </p>
          </div>
        )}

        {messages.map((m) => (
          <MessageBubble
            key={m.id}
            message={m}
            run={runs.find((r) => r.id === m.run_id)}
            ideas={ideas}
            onOpenIdea={onOpenIdea}
            onShowThoughts={onShowThoughts}
            drafts={drafts}
          />
        ))}

        {/* A run that has started but whose reply is not on the transcript yet.
            It shows its **tree**, not a stage list: the stages say where the agent
            is, the tree says what it is considering, and only the second is worth
            watching. */}
        {runs.filter((r) => !messages.some((m) => m.role === "agent" && m.run_id === r.id))
          .map((r) => (
            <div className="msg msg--agent" key={r.id}>
              <div className="msg-role">
                <Icon name="robot" /> creative thinker
                <button className="msg-run-link" onClick={onShowThoughts}
                  title="Open the tree of thoughts in a window">
                  open <Icon name="arrow-up-right" size={12} />
                </button>
              </div>
              <LiveThoughts run={r} />
            </div>
          ))}
        <div ref={endRef} />
      </div>

      <div className={"composer" + (dropping ? " composer--drop" : "")}>
        {staged.length > 0 && (
          <div className="staged">
            <div className="staged-label">
              Added to the next query
              <span className="staged-note">
                stays here until you send — then it joins the session context
              </span>
            </div>
            <div className="staged-chips">
              {staged.map((c) => (
                <span className={"ctx-chip ctx-chip--" + c.kind} key={`${c.kind}:${c.id}`}>
                  <span className={"ctx-kind ctx-kind--" + c.kind}>{c.kind}</span>
                  <span className="ctx-chip-label" title={c.digest || c.id}>{c.label || c.id}</span>
                  <button className="ctx-chip-x" onClick={() => onUnstage(c)} title="Remove">
                    <Icon name="x" size={12} />
                  </button>
                </span>
              ))}
            </div>
          </div>
        )}

        <div className="composer-row">
          <button className="composer-plus" onClick={() => setPicker(true)} title="Add context">
            <Icon name="plus" />
          </button>
          <textarea
            className="composer-text"
            rows={3}
            value={text}
            placeholder="Ask for something — “generate three ways to attack this gap”, “would this hold for video?”, “make it cheaper”…"
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit(); }
            }}
            onPaste={(e) => {
              // Pasting a bare id is one of the four documented ways context enters.
              const t = e.clipboardData.getData("text/plain").trim();
              const kind = t.startsWith("gap_") ? "gap" : t.startsWith("idea_") ? "idea"
                : /^(arxiv_|doi_|paper_)/.test(t) ? "paper" : null;
              if (kind && !t.includes(" ")) {
                e.preventDefault();
                onStage([{ kind, id: t, label: t, digest: "" }]);
              }
            }}
          />
          <button className="btn btn--primary btn--circle composer-send"
            onClick={submit} disabled={sending || (!text.trim() && !staged.length)}
            title="Send (↵)">
            <Icon name={sending ? "clock" : "arrow-right"} />
          </button>
        </div>

        <Controls settings={settings} ideas={ideas} onSettings={onSettings} />
      </div>

      {picker && (
        <ContextPicker
          loaded={session?.context ?? []}
          staged={staged}
          onClose={() => setPicker(false)}
          onAdd={onStage}
        />
      )}
    </section>
  );
}

// --- the composer's controls ---------------------------------------------------

function Controls({
  settings, ideas, onSettings,
}: {
  settings: ComposerSettings; ideas: Idea[];
  onSettings: (p: Partial<ComposerSettings>) => void;
}) {
  const [stylesOpen, setStylesOpen] = useState(false);
  return (
    <div className="controls">
      <label className="ctl">
        <span className="ctl-label">Mode</span>
        <select className="ctl-select" value={settings.mode}
          onChange={(e) => onSettings({ mode: e.target.value as ComposerSettings["mode"] })}>
          <option value="generation">generation — new candidates</option>
          <option value="refinement">refinement — develop one idea</option>
        </select>
      </label>

      {settings.mode === "refinement" && (
        <label className="ctl">
          <span className="ctl-label">Idea</span>
          <select className="ctl-select" value={settings.ideaId ?? ""}
            onChange={(e) => onSettings({ ideaId: e.target.value || null })}>
            <option value="">— pick an idea —</option>
            {ideas.map((i) => <option key={i.id} value={i.id}>{i.short}</option>)}
          </select>
        </label>
      )}

      <label className="ctl ctl--num">
        <span className="ctl-label" title="branches per expansion">Width</span>
        <select className="ctl-select" value={settings.width}
          onChange={(e) => onSettings({ width: Number(e.target.value) })}>
          {[1, 2, 3, 4, 5, 6].map((n) => <option key={n} value={n}>{n}</option>)}
        </select>
      </label>

      <label className="ctl ctl--num">
        <span className="ctl-label" title="refinement only — 1 is a plain chain of thought">Depth</span>
        <select className="ctl-select" value={settings.depth} disabled={settings.mode === "generation"}
          onChange={(e) => onSettings({ depth: Number(e.target.value) })}>
          {[1, 2, 3, 4, 5].map((n) => <option key={n} value={n}>{n}</option>)}
        </select>
      </label>

      <div className="ctl ctl--styles">
        <button className="ctl-select ctl-styles-btn" onClick={() => setStylesOpen(!stylesOpen)}>
          {settings.styles.length ? settings.styles.join(", ") : "styles: roster order"}
          <Icon name="chevron-down" size={13} />
        </button>
        {stylesOpen && (
          <div className="ctl-menu" onMouseLeave={() => setStylesOpen(false)}>
            <div className="ctl-menu-note">A style is a way of thinking, not a temperature knob.</div>
            {STYLES.map((s) => (
              <label key={s} className="ctl-menu-row">
                <input type="checkbox" checked={settings.styles.includes(s)}
                  onChange={(e) => onSettings({
                    styles: e.target.checked
                      ? [...settings.styles, s]
                      : settings.styles.filter((x) => x !== s),
                  })} />
                {s}
              </label>
            ))}
            {settings.styles.length > 0 && (
              <button className="ctl-menu-clear" onClick={() => onSettings({ styles: [] })}>clear</button>
            )}
          </div>
        )}
      </div>

      <label className="ctl ctl--check" title="let a branch ask the Investigator for a fact instead of inventing it">
        <input type="checkbox" checked={settings.toolUse}
          onChange={(e) => onSettings({ toolUse: e.target.checked })} />
        <span className="ctl-label">tools</span>
      </label>
    </div>
  );
}

// --- one transcript message ----------------------------------------------------

function MessageBubble({
  message, run, ideas, onOpenIdea, onShowThoughts, drafts,
}: {
  message: ChatMessage; run?: RunState; ideas: Idea[];
  onOpenIdea: (id: string) => void; onShowThoughts: () => void;
  drafts: DraftActions;
}) {
  if (message.role === "user") {
    return (
      <div className="msg msg--user">
        <div className="msg-body">{message.text}</div>
        {message.context && message.context.length > 0 && (
          <div className="msg-context">
            {message.context.map((c) => (
              <span className={"ctx-chip ctx-chip--" + c.kind} key={`${c.kind}:${c.id}`}>
                <span className={"ctx-kind ctx-kind--" + c.kind}>{c.kind}</span>
                <span className="ctx-chip-label">{c.label}</span>
              </span>
            ))}
          </div>
        )}
        {message.settings && (
          <div className="msg-settings">
            {message.settings.mode}
            {message.settings.mode === "refinement" && message.settings.ideaId
              ? ` · ${message.settings.ideaId}` : ""}
            {` · width ${message.settings.width}`}
            {message.settings.mode === "refinement" ? ` · depth ${message.settings.depth}` : ""}
            {message.settings.styles.length ? ` · ${message.settings.styles.join(", ")}` : ""}
          </div>
        )}
      </div>
    );
  }

  const baseline = message.envelope?.idea.idea_id
    ? ideas.find((i) => i.id === message.envelope!.idea.idea_id) ?? null
    : null;

  return (
    <div className="msg msg--agent">
      <div className="msg-role">
        <Icon name="robot" /> creative thinker
        {message.run_id && (
          <button className="msg-run-link" onClick={onShowThoughts}
            title="Open the tree of thoughts in a window">
            thoughts <Icon name="arrow-up-right" size={12} />
          </button>
        )}
      </div>
      {message.envelope ? (
        <EnvelopeResult envelope={message.envelope} runId={message.run_id || ""}
          baseline={baseline} onOpenIdea={onOpenIdea} drafts={drafts} />
      ) : (
        <div className={"msg-body" + (message.failed ? " msg-body--failed" : "")}>{message.text}</div>
      )}
      {run && run.status !== "done" && !message.envelope && <LiveThoughts run={run} />}
    </div>
  );
}
