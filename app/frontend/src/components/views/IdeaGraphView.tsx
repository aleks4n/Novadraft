// The full idea-graph page — what the sidebar's *Research Ideas* header opens.
// Gaps across the top, the ideas that attack them below, typed edges between.
// Clicking a node opens its page; the graph itself stays a map, not an editor.
import { Icon } from "../Icon";
import { ViewHeader } from "../chrome";
import { IdeaGraph } from "../IdeaGraph";
import { useWorkspace } from "../../lib/store";
import { graphGaps } from "../../lib/graphdata";

export function IdeaGraphView() {
  const { active, bundle, setNav } = useWorkspace();
  const ideas = bundle?.ideas ?? [];
  const gaps = graphGaps(bundle);

  return (
    <div className="view view--graph">
      <ViewHeader
        eyebrow={`${gaps.length} gaps · ${ideas.length} ideas`}
        title="Idea Graph"
        subtitle="Ideas are stored as a graph, not a list: each one is connected to the gaps it attacks and to the ideas it descends from, varies or merges into. Every edge carries the rationale that created it — hover to read it."
        actions={
          <>
            <button className="btn btn--ghost" onClick={() => setNav({ view: "ideas", id: null })}>
              <Icon name="list" /> List view
            </button>
            <button className="btn btn--primary" onClick={() => setNav({ view: "sessions", id: null })}>
              <Icon name="sparkle" /> New session
            </button>
          </>
        }
        breadcrumb={[
          { label: "Research" },
          { label: active?.shortTitle || "Topic", onClick: () => setNav({ view: "topic", id: null }) },
          { label: "Idea graph", current: true },
        ]}
      />

      <div className="graph-page">
        <IdeaGraph
          gaps={gaps}
          ideas={ideas}
          onOpen={(kind, id) => setNav({ view: kind === "gap" ? "gap" : "idea", id })}
        />
      </div>
    </div>
  );
}
