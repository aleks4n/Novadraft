// Which gaps the idea graph draws.
//
// The master list is the live, validated one — what an idea is allowed to address
// — but an idea may cite a gap that has since moved stage or been archived as a
// duplicate, and dropping it would cut the edge that explains why the idea exists.
// So: every master gap, plus any other gap some idea addresses, in master-first
// order.
import type { Gap, TopicBundle } from "./types";

export function graphGaps(bundle: TopicBundle | null): Gap[] {
  if (!bundle) return [];
  const all = bundle.allGaps ?? bundle.gaps ?? [];
  const master = all.filter((g) => (g.stage ?? g.ingestion?.stage) === "master");
  const addressed = new Set(bundle.ideas.flatMap((i) => i.addresses ?? []));
  const extra = all.filter(
    (g) => addressed.has(g.id) && !master.some((m) => m.id === g.id),
  );
  return [...master, ...extra];
}
