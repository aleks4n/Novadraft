// Visualises a paper's persisted lifecycle (paper.json `processing`): the five
// stages fetch → extract → summarize → verify → gaps. Each stage is simply done
// or not — the flags are independent (a paper can have gaps extracted without a
// verified summary), so we don't infer a single "current" position here; that
// only belongs to a live run (see RunTranscript). `compact` is the row variant.
import { Icon } from "./Icon";
import type { PaperStage } from "../lib/types";

export function PaperPipeline({ stages, compact = false }: { stages: PaperStage[]; compact?: boolean }) {
  if (!stages?.length) return null;

  if (compact) {
    return (
      <div className="ppipe--compact" title={stages.map((s) => `${s.label}: ${s.done ? "✓" : "—"}`).join("  ")}>
        {stages.map((s) => (
          <span key={s.key} className={"ppipe-seg" + (s.done ? " ppipe-seg--done" : "")} />
        ))}
      </div>
    );
  }

  return (
    <div className="ppipe">
      {stages.map((s, i) => (
        <div key={s.key} className={"ppipe-step ppipe-step--" + (s.done ? "done" : "todo")}>
          <span className="ppipe-node">
            {s.done ? <Icon name="check" size={12} /> : <span className="ppipe-dot" />}
          </span>
          <span className="ppipe-label">{s.label}</span>
          {i < stages.length - 1 && <span className="ppipe-bar" />}
        </div>
      ))}
    </div>
  );
}
