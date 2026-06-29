import { useState } from "react";
import { Icon } from "../Icon";
import { ViewHeader } from "../chrome";
import { RunTranscript } from "../RunTranscript";
import { useWorkspace } from "../../lib/store";

export function NewIdea() {
  const { active, bundle, run, startIdeaRun, setNav } = useWorkspace();
  const [sketch, setSketch] = useState("");
  const running = run.kind === "idea" && run.status === "running";

  const submit = () => {
    if (!sketch.trim() || running) return;
    startIdeaRun({ mode: "expand", sketch });
  };

  // A couple of starting points drawn from the topic's real gaps.
  const gapSeeds = (bundle?.gaps ?? []).slice(0, 4);

  return (
    <div className="view view--newidea">
      <ViewHeader
        eyebrow="New ideation"
        title="Spark a research idea"
        subtitle="Describe a direction in your own words. The agent grounds it in this topic's stated gaps, sharpens the approach, scores it, and drafts a method — streaming each step below."
        breadcrumb={[
          { label: "Research" },
          { label: "Ideas", onClick: () => setNav({ view: "ideas", id: null }) },
          { label: "New", current: true },
        ]}
      />

      <div className="newidea-shell">
        <textarea
          className="newidea-text"
          placeholder="What's the idea? — e.g. 'Share a single attention sparsity pattern across all layers, learned jointly, so long-context compute scales sub-quadratically without per-layer tuning.'"
          rows={7}
          value={sketch}
          onChange={(e) => setSketch(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) submit(); }}
          disabled={running}
        />

        <div className="newidea-foot">
          <span className="newidea-pill">
            <Icon name="target" /><span>Topic: <b>{active?.shortTitle || "—"}</b></span>
          </span>
          <div className="newidea-foot-right">
            <button className="btn btn--primary btn--circle" onClick={submit} disabled={running || !sketch.trim()} title="Develop idea (⌘↵)">
              <Icon name={running ? "clock" : "arrow-right"} />
            </button>
          </div>
        </div>

        {run.kind === "idea" && run.status !== "idle" && <RunTranscript run={run} />}
      </div>

      {gapSeeds.length > 0 && (
        <div className="newidea-suggest">
          <div className="newidea-suggest-label">Or start from a stated gap</div>
          <div className="newidea-suggest-grid">
            {gapSeeds.map((g) => (
              <button key={g.id} className="suggest-card" disabled={running}
                onClick={() => setSketch(`Address the gap: ${g.title}. ${g.description}`)}>
                <div className="suggest-title">{g.short}</div>
                <div className="suggest-from"><Icon name="compass" /> {g.type.replace(/_/g, " ")}</div>
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
