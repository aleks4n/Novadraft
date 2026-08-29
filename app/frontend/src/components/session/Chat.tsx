// The session's conversation — always present, always on the left.
//
// The idea agent plans each turn from the message and the session's loaded context.
// It may answer directly, investigate, call the creative thinker, or combine them.
import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { Icon } from "../Icon";
import { LiveThoughts } from "../LiveThoughts";
import { RichText } from "../RichText";
import { ContextPicker } from "./ContextPicker";
import { EnvelopeResult, OfferedDrafts } from "./EnvelopeResult";
import type { DraftActions } from "./EnvelopeResult";
import { readDrop } from "../../lib/dnd";
import type { ChatMessage, ContextRef, Idea, RunState, Session } from "../../lib/types";

export function Chat({
  session, staged, runs, ideas, sending,
  onStage, onUnstage, onUploadFile, onSend, onOpenIdea, onShowThoughts, drafts, grip,
}: {
  session: Session | null;
  staged: ContextRef[];
  runs: RunState[];
  ideas: Idea[];
  sending: boolean;
  onStage: (refs: ContextRef[]) => void;
  onUnstage: (ref: ContextRef) => void;
  onUploadFile: (file: File) => void;
  onSend: (text: string) => void;
  onOpenIdea: (id: string) => void;
  onShowThoughts: () => void;
  drafts: DraftActions;
  /** The grid's reorder handle — the chat is a draggable window like the rest. */
  grip?: ReactNode;
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
        {grip}
        <span className="panel-kind">Chat</span>
        <span className="agent-chip"
          title="Plans from your message and loaded context, then calls the investigator or creative thinker when needed.">
          <Icon name="sparkle" size={13} /> idea agent
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

        {/* A run that has started but whose reply is not on the transcript yet. */}
        {runs.filter((r) => !messages.some((m) => m.role === "agent" && m.run_id === r.id))
          .map((r) => (
            <div className="msg msg--agent" key={r.id}>
              <div className="msg-role">
                <Icon name="robot" /> idea agent
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

// --- one transcript message ----------------------------------------------------

function MessageBubble({
  message, run, ideas, onOpenIdea, onShowThoughts, drafts,
}: {
  message: ChatMessage; run?: RunState; ideas: Idea[];
  onOpenIdea: (id: string) => void; onShowThoughts: () => void;
  drafts: DraftActions;
  /** The grid's reorder handle — the chat is a draggable window like the rest. */
  grip?: ReactNode;
}) {
  if (message.role === "user") {
    return (
      <div className="msg msg--user">
        <div className="msg-body"><RichText>{message.text}</RichText></div>
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
        {message.settings?.mode && (
          <div className="msg-settings">
            {message.settings.mode}
            {message.settings.mode === "refinement" && message.settings.ideaId
              ? ` · ${message.settings.ideaId}` : ""}
            {` · width ${message.settings.width}`}
            {message.settings.mode === "refinement" ? ` · depth ${message.settings.depth}` : ""}
            {message.settings.styles?.length ? ` · ${message.settings.styles.join(", ")}` : ""}
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
        <Icon name="robot" /> idea agent
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
        <>
          <div className={"msg-body" + (message.failed ? " msg-body--failed" : "")}>
            <RichText>{message.text}</RichText>
          </div>
          {message.run_id && <OfferedDrafts runId={message.run_id} drafts={drafts} />}
        </>
      )}
      {run && run.status !== "done" && !message.envelope && <LiveThoughts run={run} />}
    </div>
  );
}
