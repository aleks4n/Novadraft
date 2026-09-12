// Segment-level diff between two versions of one field or section.
//
// A run's result view used to say only *that* a block was rewritten. For a
// six-line description that is close to useless: the reader has to re-read the
// whole block and reconstruct the change themselves. So the block is rendered as
// the diff — what the run deleted struck through, what it wrote marked — and the
// prose that survived untouched stays plain, which is what makes the change
// visible at a glance.
//
// The unit is a **segment**: a list item, a markdown line, or a sentence inside a
// line. Lines alone are the wrong granularity for prose (a rewritten paragraph is
// one line, so a line diff reports "everything changed" and says nothing); words
// are too fine (a re-worded sentence becomes confetti). Sentences are the unit a
// researcher actually compares.

export type DiffOp = "same" | "ins" | "del";

export interface DiffSegment {
  op: DiffOp;
  text: string;
}

/** Split a value into the units the diff compares. */
export function segment(value: string | string[]): string[] {
  if (Array.isArray(value)) return value.map((v) => String(v).trim()).filter(Boolean);
  return String(value ?? "")
    .split(/\n+/)
    .flatMap((line) => line.split(/(?<=[.!?])\s+/))
    .map((s) => s.trim())
    .filter(Boolean);
}

// Compared loosely — a segment that only moved onto another line, or picked up
// different spacing, is not a change anyone wants marked.
const norm = (s: string) => s.replace(/\s+/g, " ").trim();

/** Longest-common-subsequence diff over segments, in reading order. */
export function diffSegments(before: string | string[], after: string | string[]): DiffSegment[] {
  const a = segment(before);
  const b = segment(after);
  const na = a.map(norm);
  const nb = b.map(norm);

  // lcs[i][j] = length of the longest common subsequence of a[i:] and b[j:]
  const lcs: number[][] = Array.from({ length: a.length + 1 }, () => new Array(b.length + 1).fill(0));
  for (let i = a.length - 1; i >= 0; i--) {
    for (let j = b.length - 1; j >= 0; j--) {
      lcs[i][j] = na[i] === nb[j] ? lcs[i + 1][j + 1] + 1 : Math.max(lcs[i + 1][j], lcs[i][j + 1]);
    }
  }

  const out: DiffSegment[] = [];
  let i = 0;
  let j = 0;
  while (i < a.length && j < b.length) {
    if (na[i] === nb[j]) { out.push({ op: "same", text: b[j] }); i++; j++; }
    // A deletion is emitted before the insertion that replaced it, so a reworded
    // sentence reads as old-then-new rather than as two unrelated marks.
    else if (lcs[i + 1][j] >= lcs[i][j + 1]) { out.push({ op: "del", text: a[i] }); i++; }
    else { out.push({ op: "ins", text: b[j] }); j++; }
  }
  for (; i < a.length; i++) out.push({ op: "del", text: a[i] });
  for (; j < b.length; j++) out.push({ op: "ins", text: b[j] });
  return out;
}

/** How much of the new version is actually new — for the "n of m lines" note. */
export function diffCounts(segments: DiffSegment[]) {
  return {
    added: segments.filter((s) => s.op === "ins").length,
    removed: segments.filter((s) => s.op === "del").length,
    same: segments.filter((s) => s.op === "same").length,
  };
}
