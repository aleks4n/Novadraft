// Visualises a paper's persisted lifecycle (paper.json `processing`): the five
// stages fetch → extract → summarize → verify → gaps. Each stage is simply done
// or not — the flags are independent (a paper can have gaps extracted without a
// verified summary), so we don't infer a single "current" position here; that
// only belongs to a live run (see RunTranscript). `compact` is the row variant.
import { Icon } from "./Icon";
import type { GapStep, PaperStage } from "../lib/types";

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

// A gap's ingestion lifecycle (mined → deduped → validated → ranked), rendered as
// the same horizontal strip as PaperPipeline. Each step carries a richer state
// (a duplicate skips validate/rank; the active step highlights during a live run).
export function GapPipeline({ steps }: { steps: GapStep[] }) {
  if (!steps?.length) return null;
  return (
    <div className="ppipe">
      {steps.map((s, i) => (
        <div key={s.key} className={"ppipe-step ppipe-step--" + s.state}>
          <span className="ppipe-node">
            {s.state === "done" ? <Icon name="check" size={12} />
              : s.state === "active" ? <span className="ppipe-spin" />
                : <span className={"ppipe-dot" + (s.state === "skipped" ? " ppipe-dot--skip" : "")} />}
          </span>
          <span className="ppipe-label">{s.label}</span>
          {i < steps.length - 1 && <span className="ppipe-bar" />}
        </div>
      ))}
    </div>
  );
}
