// Which run's tree of thoughts the *Tree of thoughts* window is currently showing.
//
// The window and the chat card render the same tree from the same run, so the card
// hides itself while the window is open.
//
// A context rather than a prop because the card is four levels below the session
// (SessionWorkspace → PanelGrid → Chat → LiveThoughts → the card).
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
