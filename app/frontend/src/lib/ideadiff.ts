// What a creative-thinker run changed about an idea.
//
// The envelope carries a *modified copy* of the record — the agent never writes,
// so before an accept there are two versions of the same idea side by side. The
// session's result view renders the copy as an idea page with every changed part
// marked, which is the only honest way to show "here is what it did": a prose
// summary of a rewrite is not the rewrite.

import type { Idea, IdeaRecordRaw, IdeaSection } from "./types";

export type ChangeKind = "added" | "changed" | "removed";

export interface IdeaDiff {
  created: boolean;                       // a draft — no workspace identity yet
  fields: Map<string, ChangeKind>;        // title · oneLiner · description · problem
  sections: Map<string, ChangeKind>;      // by section slug
  // The *previous* text of everything marked `changed`, so the view can render the
  // block as a diff rather than only badging it. Without this the reader is told a
  // paragraph was rewritten and left to find the rewrite themselves.
  before: {
    fields: Map<string, string>;
    sections: Map<string, string | string[]>;
  };
}

export const EMPTY_DIFF: IdeaDiff = {
  created: false, fields: new Map(), sections: new Map(),
  before: { fields: new Map(), sections: new Map() },
};

/** A run that made this from nothing: everything is new, so nothing is marked. */
export const CREATED_DIFF: IdeaDiff = { ...EMPTY_DIFF, created: true };

const SECTION_LABELS: Record<string, string> = {
  technique: "Technique",
  rationale: "Rationale",
  open_questions: "Open questions",
  method: "Method",
  evaluation_plan: "Evaluation plan",
  datasets: "Datasets",
  risks: "Risks",
  related_work: "Related work",
};

function label(key: string) {
  return SECTION_LABELS[key] || key.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
}

function asSection(key: string, value: unknown): IdeaSection {
  const isList = Array.isArray(value);
  return {
    key,
    label: label(key),
    kind: isList ? "list" : "markdown",
    value: isList ? (value as unknown[]).map(String) : String(value ?? ""),
  };
}

/** An envelope's raw IdeaRecord → the same view model the idea page renders.
 *  Mapping it here (rather than round-tripping through the backend) is what lets
 *  the result show *before* anything is written — nothing has been accepted yet. */
export function recordToIdea(raw: IdeaRecordRaw, fallbackId = "draft"): Idea {
  // `section_order` decides the reading order, with anything it does not name
  // falling in after — the same rule the backend mapper applies, so an envelope
  // preview and the stored page never disagree about the document's structure.
  const raw_sections = (raw.sections || {}) as Record<string, unknown>;
  const order = (raw.section_order as string[] | undefined) || [];
  const keys = [...order.filter((k) => k in raw_sections),
                ...Object.keys(raw_sections).filter((k) => !order.includes(k))];
  const sections = keys.map((k) => asSection(k, raw_sections[k]));
  const ev = (raw.evaluation as Record<string, number | null> | undefined) || {};
  const origin = (raw.origin as Record<string, unknown> | null) || null;
  const relations = Array.isArray(raw.relations) ? raw.relations : [];
  return {
    id: raw.idea_id || fallbackId,
    title: raw.title || "Untitled idea",
    short: raw.title || fallbackId,
    oneLiner: raw.one_liner || "",
    description: raw.description || "",
    problem: raw.problem || "",
    state: (raw.state as Idea["state"]) || "provisional",
    addresses: raw.addresses || [],
    relations: relations as Idea["relations"],
    inheritedFrom: (raw.inherited_from as { idea_id?: string; upto_run_id?: string } | null)
      ? {
          ideaId: (raw.inherited_from as { idea_id?: string }).idea_id ?? null,
          uptoRunId: (raw.inherited_from as { upto_run_id?: string }).upto_run_id ?? null,
        }
      : null,
    sections,
    sectionOrder: keys,
    provenance: (raw.provenance as Idea["provenance"]) || [],
    origin: origin
      ? {
          runId: (origin.run_id as string) ?? null,
          nodeId: (origin.node_id as string) ?? null,
          style: (origin.style as string) || "",
          mode: (origin.mode as string) || "",
          parentIdeaId: (origin.parent_idea_id as string) ?? null,
          suggested: (origin.suggested as string) ?? null,
          inheritedFrom: (origin.inherited_from as string) ?? null,
        }
      : null,
    runCount: (raw.runs as unknown[] | undefined)?.length ?? 0,
    runs: [],
    novelty: ev.novelty ?? null,
    feasibility: ev.feasibility ?? null,
    impact: ev.significance ?? null,
    evaluation: {
      noveltyScore: ev.novelty ?? null, novelty: "",
      feasibilityScore: ev.feasibility ?? null, feasibility: "",
      impactScore: ev.significance ?? null, impact: "",
      rigourScore: null, rigour: "", risks: [],
    },
    summary: raw.one_liner || "",
    status: raw.state === "provisional" ? "draft" : "active",
    source: "agent",
    relatedGaps: raw.addresses || [],
    relatedPapers: [],
    method: [],
    createdAt: String(raw.created_at || "").slice(0, 10),
    updatedAt: String(raw.updated_at || "").slice(0, 10),
  };
}

function sameValue(a: string | string[], b: string | string[]): boolean {
  if (Array.isArray(a) || Array.isArray(b)) {
    const la = Array.isArray(a) ? a : [a];
    const lb = Array.isArray(b) ? b : [b];
    return la.length === lb.length && la.every((v, i) => v.trim() === (lb[i] ?? "").trim());
  }
  return a.trim() === b.trim();
}

/** Compare a run's copy against the record as it stands in the workspace.
 *  `before === null` means the run *created* this idea — everything is new, and
 *  the view says so once rather than marking every line. */
export function diffIdea(before: Idea | null, after: Idea): IdeaDiff {
  if (!before) return CREATED_DIFF;

  const fields = new Map<string, ChangeKind>();
  const priorFields = new Map<string, string>();
  for (const f of ["title", "oneLiner", "description", "problem"] as const) {
    const a = (before[f] || "").trim();
    const b = (after[f] || "").trim();
    if (a !== b) {
      fields.set(f, a ? (b ? "changed" : "removed") : "added");
      if (a) priorFields.set(f, a);
    }
  }

  const sections = new Map<string, ChangeKind>();
  const priorSections = new Map<string, string | string[]>();
  const beforeByKey = new Map(before.sections.map((s) => [s.key, s]));
  for (const s of after.sections) {
    const prev = beforeByKey.get(s.key);
    if (!prev) sections.set(s.key, "added");
    else if (!sameValue(prev.value, s.value)) {
      sections.set(s.key, "changed");
      priorSections.set(s.key, prev.value);
    }
  }
  for (const s of before.sections) {
    if (!after.sections.some((x) => x.key === s.key)) sections.set(s.key, "removed");
  }
  return { created: false, fields, sections,
           before: { fields: priorFields, sections: priorSections } };
}
