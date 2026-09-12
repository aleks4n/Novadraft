// The **Content** tab of an idea — the record as a researcher reads and edits it.
//
// Two parts, with deliberately different rigidity (see src/common/schemas/idea.py):
// the validated *spine* (title, one-liner, description, problem) at the top, and
// the open *sections* below as collapsible blocks. Each section toggles like a
// dropdown and edits in place; the section slugs are the agent's contract, so the
// slug stays visible. No section is privileged — `open_questions` is one like any
// other, deletable and renameable, and a run that finds it gone writes it again.
//
// The same component renders a *result* — a copy a run produced, not yet accepted
// — with every changed part marked. That is the point of `diff`: read-only, but
// showing exactly what the run touched.
import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { Icon } from "../Icon";
import { RichText } from "../RichText";
import type { Idea, IdeaSection } from "../../lib/types";
import type { ChangeKind, IdeaDiff } from "../../lib/ideadiff";
import { EMPTY_DIFF } from "../../lib/ideadiff";
import { diffCounts, diffSegments } from "../../lib/textdiff";

export interface IdeaEdits {
  title?: string;
  oneLiner?: string;
  description?: string;
  problem?: string;
  sections?: Record<string, string | string[] | null>;
}

export function IdeaContent({
  idea, editable = false, diff = EMPTY_DIFF, onSave, footer,
}: {
  idea: Idea;
  editable?: boolean;
  diff?: IdeaDiff;
  onSave?: (edits: IdeaEdits) => Promise<void> | void;
  footer?: ReactNode;
}) {
  // Sections start open when they hold something and closed when empty — an
  // empty block is noise, but a filled one the user has to click to see is worse.
  const [open, setOpen] = useState<Record<string, boolean>>({});
  useEffect(() => {
    setOpen(Object.fromEntries(idea.sections.map((s) => [s.key, hasBody(s) || diff.sections.has(s.key)])));
  }, [idea.id, idea.sections.length]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="idea-content">
      <Spine idea={idea} editable={editable} diff={diff} onSave={onSave} />

      <div className="idea-sections">
        <div className="idea-sections-head">
          <span className="card-label">Sections</span>
          <span className="idea-sections-note">
            the open part of the record — the agent writes these by slug
          </span>
        </div>
        {idea.sections.length === 0 && (
          <div className="empty-state empty-state--inline">
            No sections yet. A creative-thinker run writes them as it develops the idea.
          </div>
        )}
        {idea.sections.map((s) => (
          <SectionBlock
            key={s.key}
            section={s}
            open={open[s.key] ?? true}
            onToggle={() => setOpen((o) => ({ ...o, [s.key]: !(o[s.key] ?? true) }))}
            editable={editable}
            change={diff.sections.get(s.key)}
            before={diff.before.sections.get(s.key)}
            onSave={onSave ? (value) => onSave({ sections: { [s.key]: value } }) : undefined}
            onDelete={onSave ? () => onSave({ sections: { [s.key]: null } }) : undefined}
          />
        ))}
        {editable && onSave && <AddSection onAdd={(key) => onSave({ sections: { [key]: "" } })} />}
      </div>

      {footer}
    </div>
  );
}

function hasBody(s: IdeaSection) {
  return Array.isArray(s.value) ? s.value.length > 0 : Boolean(s.value.trim());
}

// --- the spine ----------------------------------------------------------------

function Spine({
  idea, editable, diff, onSave,
}: { idea: Idea; editable: boolean; diff: IdeaDiff; onSave?: (e: IdeaEdits) => void | Promise<void> }) {
  return (
    <div className="idea-spine">
      <Field
        label="One-liner" value={idea.oneLiner} editable={editable} change={diff.fields.get("oneLiner")}
        before={diff.before.fields.get("oneLiner")}
        placeholder="The idea in a sentence." rows={2}
        onSave={onSave ? (v) => onSave({ oneLiner: v }) : undefined}
      />
      <Field
        label="Description" value={idea.description} editable={editable} change={diff.fields.get("description")}
        before={diff.before.fields.get("description")}
        placeholder="What the idea is and how it works." rows={7}
        onSave={onSave ? (v) => onSave({ description: v }) : undefined}
      />
      <Field
        label="Problem" value={idea.problem} editable={editable} change={diff.fields.get("problem")}
        before={diff.before.fields.get("problem")}
        hint="What the idea is for — the problem it attacks. Part of the spine because the orchestrator ranks and groups on it."
        placeholder="The problem this idea attacks." rows={3}
        onSave={onSave ? (v) => onSave({ problem: v }) : undefined}
      />
    </div>
  );
}

function Field({
  label, value, editable, change, before, placeholder, rows = 4, hint, onSave,
}: {
  label: string; value: string; editable: boolean; change?: ChangeKind;
  before?: string;
  placeholder: string; rows?: number; hint?: string;
  onSave?: (v: string) => void | Promise<void>;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value);
  useEffect(() => { if (!editing) setDraft(value); }, [value, editing]);

  const commit = async () => {
    setEditing(false);
    if (draft !== value) await onSave?.(draft);
  };

  return (
    <div className={"card card--wide idea-field" + (change ? " idea-field--" + change : "")}>
      <div className="card-head-row">
        <div className="card-label">{label}</div>
        {change && <ChangeTag kind={change} before={before} after={value} />}
        {editable && onSave && !editing && (
          <button className="ghost-btn idea-edit-btn" onClick={() => setEditing(true)} title={`Edit ${label.toLowerCase()}`}>
            <Icon name="edit" />
          </button>
        )}
      </div>
      {editing ? (
        <>
          <textarea
            className="idea-textarea" autoFocus rows={rows} value={draft} placeholder={placeholder}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Escape") { setDraft(value); setEditing(false); }
              if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) commit();
            }}
          />
          <div className="idea-edit-actions">
            <button className="btn btn--ghost" onClick={() => { setDraft(value); setEditing(false); }}>Cancel</button>
            <button className="btn btn--primary" onClick={commit}>Save</button>
          </div>
        </>
      ) : change === "changed" && before !== undefined ? (
        <DiffBody before={before} after={value} kind="markdown" />
      ) : value ? (
        <div className="card-body"><RichText>{value}</RichText></div>
      ) : (
        <p className="card-body card-body--empty">{placeholder}</p>
      )}
      {hint && !editing && <div className="idea-field-hint">{hint}</div>}
    </div>
  );
}

// --- one open section ---------------------------------------------------------

function SectionBlock({
  section, open, onToggle, editable, change, before, onSave, onDelete,
}: {
  section: IdeaSection; open: boolean; onToggle: () => void; editable: boolean;
  change?: ChangeKind; before?: string | string[];
  onSave?: (value: string | string[]) => void | Promise<void>;
  onDelete?: () => void | Promise<void>;
}) {
  const asText = Array.isArray(section.value) ? section.value.join("\n") : section.value;
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(asText);
  useEffect(() => { if (!editing) setDraft(asText); }, [asText, editing]);

  const commit = async () => {
    setEditing(false);
    const next = section.kind === "list"
      ? draft.split("\n").map((l) => l.replace(/^[-*]\s*/, "").trim()).filter(Boolean)
      : draft;
    setDraft(Array.isArray(next) ? next.join("\n") : next);
    await onSave?.(next);
  };

  return (
    <div className={"idea-section" + (open ? " idea-section--open" : "") + (change ? " idea-section--" + change : "")}>
      <div className="idea-section-head">
        <button className="idea-section-toggle" onClick={onToggle} aria-expanded={open}>
          <Icon name={open ? "chevron-down" : "chevron-right"} />
          <span className="idea-section-label">{section.label}</span>
          <span className="idea-section-slug">{section.key}</span>
        </button>
        {change && <ChangeTag kind={change} before={before} after={section.value} />}
        {open && editable && onSave && !editing && (
          <button className="ghost-btn" onClick={() => setEditing(true)} title="Edit section"><Icon name="edit" /></button>
        )}
        {open && editable && onDelete && !editing && (
          <button className="ghost-btn" onClick={onDelete} title="Delete section"><Icon name="x" /></button>
        )}
      </div>

      {open && (
        <div className="idea-section-body">
          {editing ? (
            <>
              <textarea
                className="idea-textarea" autoFocus rows={Math.min(16, Math.max(4, draft.split("\n").length + 2))}
                value={draft} onChange={(e) => setDraft(e.target.value)}
                placeholder={section.kind === "list" ? "One item per line" : "Markdown"}
                onKeyDown={(e) => {
                  if (e.key === "Escape") { setDraft(asText); setEditing(false); }
                  if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) commit();
                }}
              />
              <div className="idea-edit-actions">
                <span className="idea-edit-hint">
                  {section.kind === "list" ? "one item per line" : "markdown"} · ⌘↵ to save
                </span>
                <button className="btn btn--ghost" onClick={() => { setDraft(asText); setEditing(false); }}>Cancel</button>
                <button className="btn btn--primary" onClick={commit}>Save</button>
              </div>
            </>
          ) : change === "changed" && before !== undefined ? (
            <DiffBody before={before} after={section.value} kind={section.kind} />
          ) : section.kind === "list" ? (
            (section.value as string[]).length ? (
              <ul className="bullet-list">
                {(section.value as string[]).map((v, i) => (
                  <li key={i}><span className="bullet"></span>{v}</li>
                ))}
              </ul>
            ) : <p className="card-body card-body--empty">Empty</p>
          ) : section.value ? (
            <div className="card-body"><RichText>{section.value as string}</RichText></div>
          ) : (
            <p className="card-body card-body--empty">Empty</p>
          )}
        </div>
      )}
    </div>
  );
}

function AddSection({ onAdd }: { onAdd: (key: string) => void }) {
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState("");
  const slug = name.trim().toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "");

  if (!adding) {
    return (
      <button className="idea-section-add" onClick={() => setAdding(true)}>
        <Icon name="plus" /> Add section
      </button>
    );
  }
  return (
    <div className="idea-section-add idea-section-add--open">
      <input
        className="idea-section-input" autoFocus value={name} placeholder="e.g. Evaluation plan"
        onChange={(e) => setName(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Escape") { setAdding(false); setName(""); }
          if (e.key === "Enter" && slug) { onAdd(slug); setAdding(false); setName(""); }
        }}
      />
      <span className="idea-section-slug">{slug || "slug"}</span>
      <button className="btn btn--primary" disabled={!slug}
        onClick={() => { onAdd(slug); setAdding(false); setName(""); }}>Add</button>
      <button className="btn btn--ghost" onClick={() => { setAdding(false); setName(""); }}>Cancel</button>
    </div>
  );
}

export function ChangeTag({
  kind, before, after,
}: { kind: ChangeKind; before?: string | string[]; after?: string | string[] }) {
  const labels: Record<ChangeKind, string> = {
    added: "added", changed: "rewritten", removed: "removed",
  };
  // A rewrite badge that also says *how much* — the block below shows which lines,
  // this says whether it is a two-sentence fix or a new block wearing the old name.
  const counts = kind === "changed" && before !== undefined && after !== undefined
    ? diffCounts(diffSegments(before, after))
    : null;
  return (
    <span className={"change-tag change-tag--" + kind}>
      {labels[kind]}
      {counts && (counts.added > 0 || counts.removed > 0) && (
        <span className="change-tag-counts">
          {counts.added > 0 && <span className="change-count change-count--ins">+{counts.added}</span>}
          {counts.removed > 0 && <span className="change-count change-count--del">−{counts.removed}</span>}
        </span>
      )}
    </span>
  );
}

/** A changed block rendered as the change: what the run removed struck through,
 *  what it wrote marked, and the lines it left alone plain. Read-only — this is a
 *  result view, and the diff exists to be judged, not edited. */
function DiffBody({
  before, after, kind,
}: { before: string | string[]; after: string | string[]; kind: IdeaSection["kind"] }) {
  const segments = diffSegments(before, after);

  if (kind === "list") {
    return (
      <ul className="bullet-list bullet-list--diff">
        {segments.map((seg, i) => (
          <li key={i} className={"diff-line diff-line--" + seg.op}>
            <span className="bullet" />{seg.text}
          </li>
        ))}
      </ul>
    );
  }
  return (
    <p className="card-body card-body--diff">
      {segments.map((seg, i) => (
        <span key={i} className={"diff-seg diff-seg--" + seg.op}>{seg.text}</span>
      ))}
    </p>
  );
}
