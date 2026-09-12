// Shared "everything searchable" row builder — one flat list of gaps, ideas
// and papers built from the already-loaded TopicBundle. Used by the session
// ContextPicker (add to context) and the global top-bar search (navigate to).
import { graphGaps } from "./graphdata";
import type { ContextKind, TopicBundle } from "./types";

export interface SearchRow {
  kind: ContextKind;
  id: string;
  label: string;
  sub: string;
}

export function buildSearchRows(bundle: TopicBundle | null): SearchRow[] {
  if (!bundle) return [];
  return [
    ...graphGaps(bundle).map((g) => ({
      kind: "gap" as const, id: g.id, label: g.title || g.short,
      sub: g.canonicalDescription || g.description,
    })),
    ...bundle.ideas.map((i) => ({
      kind: "idea" as const, id: i.id, label: i.title || i.short,
      sub: i.oneLiner || i.description,
    })),
    ...bundle.papers.map((p) => ({
      kind: "paper" as const, id: p.id, label: p.title || p.short,
      sub: [p.authors, p.year].filter(Boolean).join(" · "),
    })),
  ];
}

export function filterSearchRows(rows: SearchRow[], query: string, kind: "all" | ContextKind = "all"): SearchRow[] {
  const q = query.trim().toLowerCase();
  return rows.filter((r) =>
    (kind === "all" || r.kind === kind) &&
    (!q || r.label.toLowerCase().includes(q) || r.id.toLowerCase().includes(q) ||
      r.sub.toLowerCase().includes(q)));
}
