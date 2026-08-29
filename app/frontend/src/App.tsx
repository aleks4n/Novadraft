import { Fragment, useCallback, useEffect, useState } from "react";
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
import { BreadcrumbProvider, useBreadcrumb } from "./lib/breadcrumb";
import type { Nav } from "./lib/types";

export default function App() {
  return (
    <BreadcrumbProvider>
      <Shell />
    </BreadcrumbProvider>
  );
}

const SIDEBAR_MIN = 208;
const SIDEBAR_MAX = 460;
const SIDEBAR_KEY = "novadraft.sidebarWidth";

function Shell() {
  const { nav, active, loading } = useWorkspace();
  const [sidebar, setSidebar] = useState(true);
  // The rail's width is the user's, and it survives a reload — the nav tree holds
  // long paper and gap titles, and how much of them you want to read is a taste.
  const [sidebarW, setSidebarW] = useState(() => {
    const saved = Number(localStorage.getItem(SIDEBAR_KEY));
    return saved >= SIDEBAR_MIN && saved <= SIDEBAR_MAX ? saved : 256;
  });
  const onResizeDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    document.body.classList.add("is-col-resizing");
    const move = (ev: MouseEvent) => {
      // The rail starts at the app's own 10px padding, so the width is the
      // pointer's x minus that inset.
      setSidebarW(Math.min(SIDEBAR_MAX, Math.max(SIDEBAR_MIN, ev.clientX - 10)));
    };
    const up = () => {
      document.body.classList.remove("is-col-resizing");
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", up);
    };
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
  }, []);

  useEffect(() => { localStorage.setItem(SIDEBAR_KEY, String(sidebarW)); }, [sidebarW]);

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
    <div
      className={"app" + (sidebar ? "" : " app--nosidebar") + (inSession ? " app--session" : "")}
      style={sidebar ? ({ "--sidebar-w": `${sidebarW}px` } as React.CSSProperties) : undefined}
    >
      {sidebar ? (
        <>
          <Sidebar onCollapse={() => setSidebar(false)} />
          <div
            className="sidebar-resizer"
            onMouseDown={onResizeDown}
            onDoubleClick={() => setSidebarW(256)}
            role="separator"
            aria-orientation="vertical"
            title="Drag to resize · double-click to reset"
          >
            <span />
          </div>
        </>
      ) : (
        <button className="sidebar-show" title="Show sidebar (⌘\\)" onClick={() => setSidebar(true)}>
          <Icon name="chevrons-left" className="flip" />
        </button>
      )}

      <main className={"main" + (inSession ? " main--bare" : "")}>
        {!inSession && <Topbar />}

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

/** The top nav bar: the path to wherever you are, and what kind of place it is.
 *  Views publish the path through ViewHeader; this only renders it. */
function Topbar() {
  const { crumbs } = useBreadcrumb();
  return (
    <header className="topbar">
      <nav className="topbar-path">
        {crumbs.map((c, i) => (
          <Fragment key={i}>
            {i > 0 && <span className="topbar-path-sep">/</span>}
            {c.onClick ? (
              <button className="topbar-path-link" onClick={c.onClick}>{c.label}</button>
            ) : (
              <span className={"topbar-path-item" + (c.current ? " topbar-path-item--current" : "")}>{c.label}</span>
            )}
          </Fragment>
        ))}
      </nav>
      <span className="topbar-kind">Workspace</span>
    </header>
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
