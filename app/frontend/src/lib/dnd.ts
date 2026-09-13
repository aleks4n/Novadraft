// Dragging a workspace item into a session.
//
// Context enters a session by clicking an element in the panel, dragging it into
// the conversation, pasting an id, or the agent loading more mid-run. The first
// three all end up here — one MIME type, one payload shape, so any surface can be a
// drag source and the composer only has to understand one thing.

export const REF_MIME = "application/x-novadraft-ref";

export interface DragRef {
  kind: "gap" | "idea" | "paper";
  id: string;
  label: string;
}

/** Make any element a drag source for a workspace item. Spread onto the node. */
export function refDragProps(ref: DragRef) {
  return {
    draggable: true,
    onDragStart: (e: React.DragEvent) => {
      e.dataTransfer.setData(REF_MIME, JSON.stringify(ref));
      // A plain-text fallback so the id can also be dropped into a text field
      // (or another app) rather than vanishing.
      e.dataTransfer.setData("text/plain", ref.id);
      e.dataTransfer.effectAllowed = "copy";
    },
  };
}

/** Read whatever was dropped: our own refs first, then a bare id pasted as text. */
export function readDrop(e: React.DragEvent): { refs: DragRef[]; files: File[] } {
  const files = Array.from(e.dataTransfer.files || []);
  const raw = e.dataTransfer.getData(REF_MIME);
  if (raw) {
    try {
      const parsed = JSON.parse(raw);
      return { refs: Array.isArray(parsed) ? parsed : [parsed], files };
    } catch { /* fall through to the text form */ }
  }
  const text = (e.dataTransfer.getData("text/plain") || "").trim();
  const kind = text.startsWith("gap_") ? "gap"
    : text.startsWith("idea_") ? "idea"
    : /^(arxiv_|doi_|paper_)/.test(text) ? "paper" : null;
  return { refs: kind ? [{ kind, id: text, label: text }] : [], files };
}
