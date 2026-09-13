// What one creative-thinker run produced, rendered inside the chat.
//
// Everything here is a proposal: the thinker returns a modified copy of the idea
// and writes nothing. The result view shows that copy as its own idea page with
// every changed part marked, and nothing reaches the workspace until an accept.
//
// A refinement run produces the rewritten copy, the answer to the task, and — only
// when the task moved the idea far enough that overwriting would lose it — one
// suggestion to keep both. Drafts are a generation output: candidate ideas, one
// card each.
//
// Decisions are stored on the working copy, not in component state, so a reloaded
// transcript shows what was chosen instead of offering the choice again.
import { useState } from "react";
import { Icon } from "../Icon";
import { IdeaContent } from "../views/IdeaContent";
import { CREATED_DIFF, diffIdea, recordToIdea } from "../../lib/ideadiff";
import type { Envelope, Idea, SessionDraft, SuggestedOperation } from "../../lib/types";

// What a working copy can be, from the transcript's point of view: the run's
// modified copy of the idea, or one of its drafts by index. `find` is scoped by
// run id — a session holds several runs, and one run's draft must never turn up
// under another's message.
export interface DraftActions {
  find: (runId: string, kind: "create" | "update", index: number | null) => SessionDraft | undefined;
  forRun: (runId: string) => SessionDraft[];
  // `as` applies to an `update` copy: overwrite the record it was refined from, or
  // keep both by landing it as a variant.
  accept: (draftId: string, as_?: "overwrite" | "variant") => Promise<void>;
  dismiss: (draftId: string) => Promise<void>;
  busy: string | null;
}

/** Planned turns have one reply but may contain offers from several thinker calls,
 * so there is no single Envelope to render. Their working copies are already the
 * exact records the user must decide on; render those under the turn's reply. */
export function OfferedDrafts({ runId, drafts, ideas = [] }: {
  runId: string; drafts: DraftActions;
  /** The workspace's live ideas — the fallback baseline for a copy offered before
   *  `baseline_idea` was frozen onto it. Only correct until that idea is overwritten,
   *  which is exactly why newer copies carry their own. */
  ideas?: Idea[];
}) {
  const copies = drafts.forRun(runId);
  if (!copies.length) return null;
  return (
    <div className="envelope">
      <div className="env-block-label">
        Proposals · {copies.length}
        <span className="env-block-note">nothing is written until you accept it</span>
      </div>
      <div className="env-drafts">
        {copies.map((copy) => (
          <DraftCard key={copy._session.draft_id}
            draft={recordToIdea(copy, copy._session.draft_id)} copy={copy} actions={drafts}
            ideas={ideas} />
        ))}
      </div>
    </div>
  );
}

/** A decided working copy renders its decision, never the buttons again. The
 *  decision is stored on the copy, so this survives a reload — which is the point:
 *  "save as an idea" was a choice, and a transcript that forgets it is wrong. */
function decision(copy?: SessionDraft) {
  const status = copy?._session.status;
  return status === "accepted" || status === "dismissed" ? status : null;
}

export function EnvelopeResult({
  envelope, runId, baseline, onOpenIdea, drafts,
}: {
  envelope: Envelope;
  // The **run manager's** id for this run, which is what a working copy is filed
  // under. `envelope.run_id` is the thinker's own id for its trace — a different
  // namespace, and matching on it silently finds nothing.
  runId: string;
  // The idea as it stands in the workspace — the left side of the diff. `null`
  // when the run created something new rather than changing anything.
  baseline: Idea | null;
  onOpenIdea?: (id: string) => void;
  // Absent outside a session (the Ideas view shows results read-only).
  drafts?: DraftActions;
}) {
  const after = recordToIdea(envelope.idea);
  const diff = diffIdea(baseline, after);
  const changed = diff.created || diff.fields.size > 0 || diff.sections.size > 0;
  // A generation run seeded with no parent returns an *empty* record: the run's
  // result is the drafts, and the record is the envelope keeping its shape. There
  // is nothing to show there, and rendering it as a page of placeholder fields
  // reads as a broken idea rather than an absent one.
  const seeded = Boolean(
    envelope.idea.idea_id || envelope.idea.title?.trim() || envelope.idea.description?.trim());

  const updateDraft = drafts?.find(runId, "update", null);
  // The thinker raises at most one suggestion, and only this one: carrying out the
  // task moved the idea far enough that overwriting is a judgement call. It changes
  // what the accept bar offers rather than adding a second place to decide.
  const diverged = envelope.suggested_operations.some((o) => o.type === "create-variant");

  return (
    <div className="envelope">
      {envelope.answer && (
        <div className="envelope-answer">
          <div className="envelope-answer-label"><Icon name="sparkle" /> Answer</div>
          <p>{envelope.answer}</p>
        </div>
      )}

      <div className="envelope-meta">
        <span className={"env-status env-status--" + envelope.status}>{envelope.status}</span>
        {envelope.status !== "completed" && envelope.status_reason && (
          <span className="env-reason">{envelope.status_reason}</span>
        )}
        {envelope.limit && <span className="env-reason">limit: {envelope.limit}</span>}
        <span className="env-metric">{envelope.metrics.llm_calls} calls</span>
        <span className="env-metric">{envelope.metrics.nodes} thoughts</span>
        {envelope.metrics.cluster_count > 0 && (
          <span className="env-metric" title="mean pairwise distance between branches">
            diversity {envelope.metrics.mean_pairwise_distance.toFixed(2)} · {envelope.metrics.cluster_count} clusters
          </span>
        )}
      </div>

      {envelope.questions.length > 0 && (
        <div className="env-block">
          <div className="env-block-label">Open questions for you</div>
          {envelope.questions.map((q, i) => (
            <div className="env-question" key={i}>
              <div className="env-question-text">{q.question}</div>
              {q.options.length > 0 && (
                <div className="env-options">
                  {q.options.map((o, j) => (
                    <button className="env-option" key={j} disabled title="Answering is the orchestrator's job — not wired yet">
                      {o}
                    </button>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {envelope.suggested_operations.length > 0 && (
        <div className="env-block">
          <div className="env-block-label">
            Suggestion
            <span className="env-block-note">
              the run did what it was asked — this is the one thing it could not decide for you
            </span>
          </div>
          {envelope.suggested_operations.map((op, i) => <OperationCard key={i} op={op} />)}
        </div>
      )}

      {envelope.drafts.length > 0 && (
        <div className="env-block">
          <div className="env-block-label">
            Drafts · {envelope.drafts.length}
            <span className="env-block-note">no id yet — minting one is an explicit accept</span>
          </div>
          <div className="env-drafts">
            {envelope.drafts.map((d, i) => (
              <DraftCard key={i} draft={recordToIdea(d, `draft_${i}`)}
                copy={drafts?.find(runId, "create", i)} actions={drafts} />
            ))}
          </div>
        </div>
      )}

      {seeded && (
      <div className="env-block">
        <div className="env-block-label">
          {diff.created ? "New idea" : changed ? "The idea, rewritten" : "The idea, unchanged"}
          {changed && !diff.created && (
            <span className="env-block-note">
              {diff.fields.size + diff.sections.size} part{diff.fields.size + diff.sections.size === 1 ? "" : "s"} changed
            </span>
          )}
          {!changed && !diff.created && (
            <span className="env-block-note">
              a run asked a question may legitimately change nothing — the answer is above
            </span>
          )}
        </div>
        <div className="env-idea">
          <div className="env-idea-head">
            <span className="env-idea-title">{after.title}</span>
            {onOpenIdea && after.id !== "draft" && (
              <button className="ghost-btn" title="Open the stored idea"
                onClick={() => onOpenIdea(after.id)}><Icon name="arrow-up-right" /></button>
            )}
          </div>
          <IdeaContent idea={after} diff={diff} />
          {changed && !diff.created && updateDraft && drafts && (
            diverged ? (
              <AcceptBar
                actions={drafts}
                draft={updateDraft}
                acceptLabel="Overwrite the idea"
                variantLabel="Keep both — save as a variant"
                note="The run thinks this may no longer be the same idea. Keeping both writes a new
                      record with a variant-of edge back; overwriting replaces the original."
              />
            ) : (
              <AcceptBar
                actions={drafts}
                draft={updateDraft}
                acceptLabel="Accept into the idea"
                note="Overwrites the stored record — the run's thinking goes into its runs[] with it."
              />
            )
          )}
        </div>
      </div>
      )}
    </div>
  );
}

/** Accept writes to the knowledge base; dismiss deletes the working copy. The
 *  two are deliberately not symmetric in weight — one is a decision to keep. */
function AcceptBar({
  actions, draft, acceptLabel, variantLabel, note,
}: {
  actions: DraftActions; draft: SessionDraft; acceptLabel: string;
  variantLabel?: string; note?: string;
}) {
  const draftId = draft._session.draft_id;
  const busy = actions.busy === draftId;
  const decided = decision(draft);

  if (decided) {
    const landed = draft._session.result_idea_id;
    return (
      <div className="accept-bar accept-bar--decided">
        <span className={"accept-decision accept-decision--" + decided}>
          {decided === "accepted"
            ? landed ? `Saved — ${landed}` : "Saved into the workspace"
            : "Dismissed"}
        </span>
      </div>
    );
  }

  return (
    <div className="accept-bar">
      {note && <span className="accept-note">{note}</span>}
      {variantLabel && (
        <button className="btn btn--primary btn--sm" disabled={busy}
          onClick={() => actions.accept(draftId, "variant")}>
          {busy ? "Saving…" : variantLabel}
        </button>
      )}
      <button className={"btn btn--sm " + (variantLabel ? "btn--ghost" : "btn--primary")}
        disabled={busy} onClick={() => actions.accept(draftId, "overwrite")}>
        {busy ? "Saving…" : acceptLabel}
      </button>
      <button className="btn btn--ghost btn--sm" disabled={busy}
        onClick={() => actions.dismiss(draftId)} title="Records the decision; nothing is written">
        Dismiss
      </button>
    </div>
  );
}

const OP_LABEL: Record<SuggestedOperation["type"], string> = {
  "update-idea": "Update the idea",
  "create-child": "Split off as a child",
  "create-variant": "This may no longer be the same idea",
  merge: "Merge",
  investigate: "Investigate first",
};

function OperationCard({ op }: { op: SuggestedOperation }) {
  return (
    <div className={"env-op env-op--" + op.type}>
      <div className="env-op-head">
        <span className="env-op-type">{OP_LABEL[op.type] || op.type}</span>
        {op.node_id && <span className="env-op-node">{op.node_id}</span>}
      </div>
      <div className="env-op-diagnosis">{op.diagnosis}</div>
      {op.rationale && <div className="env-op-rationale">{op.rationale}</div>}
      <div className="env-op-actions">
        <span className="env-op-decided">
          Decide it below, on the rewritten idea — keep both, or overwrite.
        </span>
      </div>
    </div>
  );
}

function DraftCard({
  draft, copy, actions, ideas = [],
}: { draft: Idea; copy?: SessionDraft; actions?: DraftActions; ideas?: Idea[] }) {
  // An "update" copy has a frozen baseline_idea (the record it would overwrite, as it
  // stood when offered) to diff against; a "create" copy has nothing prior — CREATED_DIFF
  // marks nothing rather than badging every part of a brand-new idea as "changed".
  //
  // Copies offered before that field existed have no frozen baseline, and falling back to
  // the live record is better than the alternative: without it the card renders as a page
  // of unmarked prose, which reads as "the run changed nothing". The fallback goes stale
  // the moment that record is overwritten — the reason the freeze exists — so it is only
  // ever a fallback.
  const before = copy?._session.kind === "update"
    ? copy._session.baseline_idea
      ? recordToIdea(copy._session.baseline_idea)
      : ideas.find((i) => i.id === copy._session.target_idea_id) ?? null
    : null;
  const diff = before ? diffIdea(before, draft) : CREATED_DIFF;
  const changed = diff.fields.size + diff.sections.size;
  // Open a rewrite by default. A card that has to be clicked open shows a title and a
  // one-liner, which is precisely not the thing being decided — the whole reason to
  // render the copy is that the changed parts are marked inside it.
  const [open, setOpen] = useState(changed > 0);
  return (
    <div className="env-draft">
      <button className="env-draft-head" onClick={() => setOpen(!open)}>
        <Icon name={open ? "chevron-down" : "chevron-right"} />
        <span className="env-draft-title">{draft.title}</span>
        {copy?._session.kind === "update" && before && (
          // Only when a baseline was actually resolved. Saying "unchanged" off a diff
          // that had nothing to compare against would state as fact the very thing the
          // reader is here to find out.
          <span className="env-draft-note">
            {changed
              ? `${changed} part${changed === 1 ? "" : "s"} changed`
              : "unchanged"}
          </span>
        )}
      </button>
      {!open && draft.oneLiner && <p className="env-draft-line">{draft.oneLiner}</p>}
      {open && (
        <div className="env-draft-body">
          <IdeaContent idea={draft} diff={diff} />
        </div>
      )}
      {copy && actions && (
        <AcceptBar
          actions={actions}
          draft={copy}
          acceptLabel={copy._session.kind === "update" ? "Accept into the idea" : "Save as an idea"}
          note={copy._session.kind === "update"
            ? "Overwrites the stored idea with this proposed version."
            : "Mints an id and writes it into the workspace, with its edge back to the parent."}
        />
      )}
    </div>
  );
}
