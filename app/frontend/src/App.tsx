import { Icon } from "./components/Icon";
import { Sidebar } from "./components/Sidebar";
import { TopicOverview } from "./components/views/TopicOverview";
import { PapersOverview, PaperDetail } from "./components/views/Papers";
import { GapsOverview, GapDetail } from "./components/views/Gaps";
import { AddPaper } from "./components/views/AddPaper";
import { IdeasOverview, IdeaDetail } from "./components/views/Ideas";
import { NewIdea } from "./components/views/NewIdea";
import { useWorkspace } from "./lib/store";
import type { Nav } from "./lib/types";

export default function App() {
  const { nav, active, loading } = useWorkspace();

  return (
    <div className="app">
      <Sidebar />

      <main className="main">
        <header className="topbar">
          <div className="topbar-left">
            <div className="topbar-tabs">
              <button className="topbar-tab topbar-tab--active"><Icon name="grid" /> Workspace</button>
            </div>
          </div>
          <div className="topbar-right">
            <div className="search-pill">
              <Icon name="search" />
              <span className="search-pill-text">Search papers, gaps, ideas...</span>
              <span className="search-pill-kbd">⌘K</span>
            </div>
            <button className="topbar-icon-btn"><Icon name="more" /></button>
          </div>
        </header>

        <div className="content">
          <div className="content-scroll" key={(active?.slug || "") + "-" + nav.view + "-" + (nav.id || "")}>
            {loading && nav.view === "topic" ? (
              <div className="empty-state">Loading workspace…</div>
            ) : (
              <View nav={nav} />
            )}
          </div>
        </div>
      </main>
    </div>
  );
}

function View({ nav }: { nav: Nav }) {
  switch (nav.view) {
    case "topic": return <TopicOverview />;
    case "papers": return <PapersOverview />;
    case "paper": return nav.id ? <PaperDetail id={nav.id} /> : null;
    case "gaps": return <GapsOverview />;
    case "gap": return nav.id ? <GapDetail id={nav.id} /> : null;
    case "add-paper": return <AddPaper />;
    case "ideas": return <IdeasOverview />;
    case "new-idea": return <NewIdea />;
    case "idea": return nav.id ? <IdeaDetail id={nav.id} /> : null;
    default: return null;
  }
}
