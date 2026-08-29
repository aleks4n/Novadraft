// Browser-style back/forward through the nav history the store builds up in
// store.tsx (setNav pushes, these just move the pointer).
import { Icon } from "./Icon";
import { useWorkspace } from "../lib/store";

export function NavHistory() {
  const { goBack, goForward, canGoBack, canGoForward } = useWorkspace();
  return (
    <div className="navhistory">
      <button className="ghost-btn" disabled={!canGoBack} onClick={goBack} title="Back">
        <Icon name="chevron-right" className="flip" />
      </button>
      <button className="ghost-btn" disabled={!canGoForward} onClick={goForward} title="Forward">
        <Icon name="chevron-right" />
      </button>
    </div>
  );
}
