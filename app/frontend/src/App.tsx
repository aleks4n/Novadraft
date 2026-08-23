import { useEffect, useState } from "react";
import { Icon } from "./components/Icon";
import { Sidebar } from "./components/Sidebar";
import { TopicOverview } from "./components/views/TopicOverview";
import { PapersOverview, PaperDetail } from "./components/views/Papers";
import { GapsOverview, GapDetail } from "./components/views/Gaps";
import { IdeasOverview, IdeaDetail } from "./components/views/Ideas";
import { IdeaGraphView } from "./components/views/IdeaGraphView";
import { SessionWorkspace } from "./components/session/SessionWorkspace";
import { NewIdea } from "./components/views/NewIdea";
import { useWorkspace } from "./lib/store";
import type { Nav } from "./lib/types";

export default function App() {
  const { nav, active, loading } = useWorkspace();
  const [sidebar, setSidebar] = useState(true);

  // ⌘\ / Ctrl-\ toggles it — the same shortcut every editor uses, and the only
  // way back once the rail is hidden besides the floating button.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "\\" && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        setSidebar((v) => !v);
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);
  // A session is a workspace of its own: its windows carry their own titles, so
  // the app topbar above them would be a second header row saying nothing the
  // session bar does not. The session bar is the top row there.
  const inSession = nav.view === "sessions" || nav.view === "session";

  return (
    <div className={"app" + (sidebar ? "" : " app--nosidebar")}>
      {sidebar ? (
        <Sidebar onCollapse={() => setSidebar(false)} />
      ) : (
        <button className="sidebar-show" title="Show sidebar (⌘\\)" onClick={() => setSidebar(true)}>
          <Icon name="chevrons-left" className="flip" />
        </button>
      )}

      <main className="main">
        {!inSession && (
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
        )}

        <div className="content">
          {inSession ? (
            // The session workspace owns its own layout and scrolling (chat +
            // resizable panels), so it replaces the page scroller rather than
            // living inside it.
            // Deliberately *not* keyed by the session id: sending the first turn
            // of a new session mints one, and remounting there would throw away
            // the window layout the user had just arranged. The workspace resets
            // the per-session state (transcript, staged context) itself.
            <SessionWorkspace sessionId={nav.id} />
          ) : (
            <div className="content-scroll" key={(active?.slug || "") + "-" + nav.view + "-" + (nav.id || "")}>
              {loading && nav.view === "topic" ? (
                <div className="empty-state">Loading workspace…</div>
              ) : (
                <View nav={nav} />
              )}
            </div>
          )}
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
    case "ideas": return <IdeasOverview />;
    case "idea-graph": return <IdeaGraphView />;
    case "new-idea": return <NewIdea />;
    case "idea": return nav.id ? <IdeaDetail id={nav.id} /> : null;
    default: return null;
  }
}
