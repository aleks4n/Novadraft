// Which run's tree of thoughts the *Tree of thoughts* window is currently showing.
//
// The window and the chat card render the same tree from the same run, so with the
// window open the same branches sit on screen twice, side by side, looking like the
// thinker ran twice. The card is the one that gives way: it is the smaller of the two
// and it owns the button that opened the window in the first place.
//
// A context rather than a prop because the card is four levels below the session
// (SessionWorkspace → PanelGrid → Chat → LiveThoughts → the card), and none of the
// levels in between has any business knowing about window layout.
import { createContext, useContext } from "react";
import type { RunState } from "../../lib/types";

/** The run id the open window mirrors, or null when no window is open. */
export const ThoughtsMirror = createContext<string | null>(null);

/** The run the *Tree of thoughts* window shows: the session's latest, the one whose
 *  branches are still arriving. Shared with `ThoughtsPanel` so "what the window shows"
 *  is defined once. */
export function mirroredRun(runs: RunState[]): RunState | undefined {
  return runs[runs.length - 1];
}

/** True when this run's tree is already on screen in the window. */
export function useMirroredInPanel(runId: string): boolean {
  return useContext(ThoughtsMirror) === runId;
}
