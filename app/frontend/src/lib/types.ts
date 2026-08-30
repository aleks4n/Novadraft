// TS mirror of the backend mapping (app/backend/mappers.py). Fields the current
// agents don't produce are optional/nullable so views hide the widget rather
// than render an empty value.

export interface TopicStats {
  papers: number;
  gaps: number;
  ideas: number;
  sessionsThisWeek: number;
}

// A foundational paper of the field, written by the category agent into
// meta.json. Clicking one ingests it through the paper pipeline (title = query).
export interface SeminalPaper {
  title: string;
  authors: string;
  year: number | null;
  why: string;
}

export interface TopicMeta {
  id: string;
  slug: string;
  title: string;
  shortTitle: string;
  category: string;
  categoryColor: string;
  description: string;
  createdAt: string;
  updatedAt: string;
  starred: boolean;
  goals: string[];
  keywords: string[];
  seminalPapers: SeminalPaper[];
  collaborators: { name: string; initials: string; color: string }[];
  stats: TopicStats;
}

export interface PaperMetric {
  name: string;
  value: string;
  dataset: string;
  comparison?: string;
}

export interface PaperSurvey {
  summary: string;
  strengths: string[];
  weaknesses: string[];
  relevance: number | null;
  paperType?: string;
  keywords?: string[];
  problem?: string;
  motivation?: string;
  priorLimitations?: string[];
  resultsHeadline?: string;
  resultsCaveats?: string[];
  datasets?: string[];
  metrics?: PaperMetric[];
}

// One step of the paper agent's persisted lifecycle (paper.json `processing`).
export interface PaperStage {
  key: string;
  label: string;
  done: boolean;
}

// The summarize agent's self-check, surfaced on the paper detail.
export interface PaperVerification {
  status: string | null; // "ok" | "issues"
  attempts: number | null;
  regenerated: boolean;
  groundedClaims: number;
  unmatchedClaims: string[];
  issueCount: number;
}

export interface Paper {
  id: string;
  title: string;
  short: string;
  authors: string;
  venue: string;
  year: number | null;
  citations: number | null;
  tags: string[];
  status: string;
  stages: PaperStage[];
  complete: boolean;
  // Fetched but full text is unavailable (restricted/paywalled) and not yet
  // extracted — the UI offers a drag-and-drop to supply a pdf/tex source.
  needsSource: boolean;
  resumeQuery: string;
  gapCount: number;
  warnings: string[];
  addedAt: string;
  addedBy: string;
  repo: { url: string | null } | null;
  hasSummary: boolean;
  // full=true only:
  abstract?: string;
  keyFindings?: string[];
  identifiers?: Record<string, string | null>;
  sourceUrl?: string | null;
  verification?: PaperVerification | null;
  survey?: PaperSurvey | null;
}

export interface GapEvidence {
  paperId: string | null;
  section: string | null;
  quote: string | null;
  charOffset: [number, number] | null;
  grounding: "matched" | "ambiguous" | "not_found" | null;
}

// One step of a gap's ingestion lifecycle (mined → deduped → validated → ranked).
export interface GapStep {
  key: "mined" | "deduped" | "validated" | "ranked";
  label: string;
  done: boolean;
  state: "done" | "active" | "todo" | "skipped";
}

export interface GapIngestion {
  stage: string | null;   // pending | to_validate | to_rank | master | archive
  // The branch a gap took: duplicate (director), or the validator's verdict.
  outcome: "novel" | "duplicate" | "open" | "partially_solved" | "solved" | null;
  duplicateOf: string | null;
  steps: GapStep[];
}

// One paper the validator judged against the gap.
export interface GapAssessment {
  paperId: string | null;
  title: string | null;
  url: string | null;
  relevance: "direct_solution" | "partial_solution" | "not_addressed" | null;
  contribution: string;
  remainsOpen: string;
}

export interface GapVerdict {
  status: "open" | "partially_solved" | "solved" | null;
  confidence: number | null;
  reasoning: string;
  addressedBy: GapAssessment[];
}

export interface GapRanking {
  openness: number | null;
  impact: number | null;
  feasibility: number | null;
  feasibilityReasoning: string;
}

export interface Gap {
  id: string;
  title: string;
  short: string;
  description: string;
  type: string;
  severity: "high" | "medium" | "low";
  canonicalDescription: string;
  derivedFrom: string[];
  evidence: GapEvidence[];
  novelty: number | null;
  feasibility: number | null;
  questions: string[];
  relatedIdeas: string[];
  status: string;
  extractedAt: string;
  // ingestion pipeline surface (director / validator / ranking)
  stage?: string | null;
  ingestion?: GapIngestion;
  duplicateOf?: string | null;
  verdict?: GapVerdict | null;
  ranking?: GapRanking | null;
}

export interface IdeaEvaluation {
  noveltyScore: number | null;
  novelty: string;
  feasibilityScore: number | null;
  feasibility: string;
  impactScore: number | null;
  impact: string;
  rigourScore: number | null;
  rigour: string;
  risks: string[];
}

// One section of an idea's *open* part. The stored `sections` map is deliberately
// unvalidated, so the backend hands it over as an ordered list with a render kind
// (`markdown` or `list`) instead — the UI never has to guess.
export interface IdeaSection {
  key: string;
  label: string;
  kind: "markdown" | "list";
  value: string | string[];
}

export interface IdeaRelation {
  type: "child-of" | "variant-of" | "merged-into" | "inspired-by";
  target: string;
  rationale: string;
  run: string | null;
}

export interface ThoughtNode {
  id: string;
  parentId: string | null;
  text: string;
  title: string;
  style: string;
  status: "kept" | "pruned" | "rejected" | "accepted";
  kind: "root" | "candidate" | "branch" | "aggregate";
  depth: number;
  comparison: string;
  question: string | null;
}

export interface ThoughtEdge {
  from: string;
  to: string;
  type: "expanded-into" | "aggregated-from";
}

// One creative-thinker invocation as stored on the idea (`runs[]`, append-only).
export interface IdeaRun {
  runId: string;
  sessionId: string | null;
  timestamp: string;
  mode: string;
  width: number | null;
  depth: number | null;
  model: string;
  styles: string[];
  task: string;
  triggeringAction: string;
  contextIds: string[];
  nodes: ThoughtNode[];
  edges: ThoughtEdge[];
  outcome: {
    status: "completed" | "stopped-uncertain" | "truncated";
    reason: string;
    limit: string | null;
    acceptedNode: string | null;
    acceptedNodes: string[];
    escalation: string | null;
    escalationNode: string | null;
    escalationDiagnosis: string;
    answer: string;
    /** Changes the run recommended to the idea but did not make, one line each. */
    proposedChanges: string[];
    truncated: boolean;
    summary: { objective: string; accepted: string; rejected: string; open: string[] };
  };
}

export interface IdeaOrigin {
  runId: string | null;
  nodeId: string | null;
  style: string;
  mode: string;
  parentIdeaId: string | null;
  suggested: string | null;
  inheritedFrom: string | null;
}

export interface Idea {
  id: string;
  title: string;
  short: string;
  // spine
  oneLiner: string;
  description: string;
  problem: string;
  state: "root" | "derived" | "merge" | "provisional" | "superseded";
  addresses: string[];
  relations: IdeaRelation[];
  // Set when this record's content was copied from a parent (a child or a
  // variant). Spine-level per the design of record: it is a claim about the
  // *record* — which parts were not independently arrived at — where `origin` is
  // a claim about the run that produced the idea.
  inheritedFrom: { ideaId: string | null; uptoRunId: string | null } | null;
  // open part
  sections: IdeaSection[];
  // The reading order `sections` is already in — sent so an edit can round-trip it.
  sectionOrder: string[];
  provenance: { claim: string; source: string; via: string }[];
  origin: IdeaOrigin | null;
  // the thinking graph — only the single-idea endpoint carries it
  runCount: number;
  runs: IdeaRun[];
  // evaluation
  novelty: number | null;
  feasibility: number | null;
  impact: number | null;
  evaluation: IdeaEvaluation;
  // Phase-1 aliases the ideas list/cards still read
  summary: string;
  status: string;
  source: string;
  relatedGaps: string[];
  relatedPapers: string[];
  method: string[];
  createdAt: string;
  updatedAt: string;
}

// View-time ranking knobs (RankingConfig's UI-facing subset). Weights feed the
// Overall score's weighted geometric mean; mins are filter floors below which a
// gap is dropped. The UI reads these as its starting values and lets the user
// tune them live.
export interface RankingConfig {
  wOpenness: number;
  wImpact: number;
  wFeasibility: number;
  minOpenness: number;
  minImpact: number;
  minFeasibility: number;
}

export interface TopicBundle {
  meta: TopicMeta;
  papers: Paper[];
  gaps: Gap[];          // pending — freshly mined, per-paper
  masterGaps: Gap[];    // curated, deduplicated master list (later phase)
  allGaps?: Gap[];      // every gap across all stages, each tagged with its stage
  ideas: Idea[];
  rankingConfig?: RankingConfig;  // view-time ranking knobs (defaults from the agent config)
}

// One flat run event (dev/runlog.py shape), forwarded over SSE.
export interface RunEvent {
  seq: number;
  t: string;
  kind: "run_start" | "node" | "llm" | "tool" | "event" | "run_end";
  agent?: string;
  workspace?: string | null;
  node?: string;
  gnode?: string;
  ns?: string;   // streamed namespace (owner:uuid[/child:uuid…]) — subgraph attribution
  name?: string;
  status?: string;
  duration_s?: number | null;
  result?: unknown;
  data?: Record<string, unknown>;
  mermaid?: string;
  output?: unknown;
  error?: string | null;
}

// A disambiguation candidate returned by an ambiguous title fetch — each carries
// an identifier the picker re-runs the search with to resolve exactly.
export interface PaperCandidate {
  title: string | null;
  authors: string[];
  year: number | null;
  arxiv_id: string | null;
  doi: string | null;
  pdf_url: string | null;
  source: string | null;
}

// Run lifecycle. Runs execute detached on the backend (they survive reloads and
// navigation); the frontend observes them. "running" is live; "ambiguous"
// (pick a candidate) and "needs_source" (drop in a pdf/tex source) are *paused*
// on user input; "cancelled" (user), "interrupted" (backend restarted mid-run)
// and "error" are resumable via restart; "done" is terminal.
export type RunStatus =
  | "running" | "done" | "error" | "cancelled" | "interrupted"
  | "ambiguous" | "needs_source";

// The backend run-manager's entry for one run (app/backend/runs.py) — persisted
// server-side, so the queue restores after a page reload.
export interface RunMeta {
  run_id: string;
  slug: string;
  agent: string;
  kind: "paper" | "idea" | "resume";
  query: string | null;
  status: RunStatus;
  // The entity this run is about, once known: a resume run knows it from the
  // start; a paper ingest attaches when the fetch stage reports its paper id.
  target?: { kind: "paper" | "gap"; id: string } | null;
  session_id?: string | null;
  envelope?: Envelope;      // creative-thinker runs: the whole result
  candidates?: PaperCandidate[];
  result_paper_id?: string | null;
  result_idea_ids?: string[];
  error?: string | null;
  note?: string | null; // "duplicate" → the query resolved to a paper already in the KB
  created_at?: string;
  updated_at?: string;
  existing?: boolean;   // start() returned an already-active run for the same query
}

// A run as the store holds it: the server meta + the streamed events (for the
// transcript, stage progress, and per-gap activity). Several can be live at once.
export interface RunState {
  id: string;
  agent: string;
  kind: "paper" | "idea" | "resume";
  status: RunStatus;
  query: string | null;
  events: RunEvent[];
  error: string | null;
  resultPaperId: string | null;
  resultIdeaIds: string[];
  candidates: PaperCandidate[];
  target?: { kind: "paper" | "gap"; id: string } | null;
  sessionId?: string | null;
  envelope?: Envelope | null;
  note?: string | null;
}


// --- sessions ------------------------------------------------------------------
// A session is the chat-like surface idea work happens in. It owns its transcript,
// its loaded context and the ids of every artefact it touched (vault: *Global
// Context & Session Loading*), stored at workspace/<slug>/sessions/<id>.json.

export type ContextKind = "gap" | "idea" | "paper" | "file" | "note";

// Reference, not payload: an id plus a short digest. Full text is re-read from the
// workspace when a run needs it, so a long session cannot blow the window.
export interface ContextRef {
  kind: ContextKind;
  id: string;
  label: string;
  digest: string;
  at?: string;
}

// --- the orchestrator's plan ---------------------------------------------------
// What the planner decided to do before it does it: a goal, the checkable conditions
// it will be judged against, and one step per subagent call with the arguments that
// call is made with. The user reads it, retunes those arguments (a creative thinker's
// width and depth above all) and presses Start — nothing runs until then.

export interface PlanStep {
  tool: string;
  args: Record<string, unknown>;
  why: string;
  needs: number[];
}

export interface AgentPlan {
  goal: string;
  steps: PlanStep[];
  done_criteria: string[];
  answer?: string;
}

// "proposed" is waiting on the user, "approved" started a second run (`plan_run_id`),
// "cancelled" was turned down. Stored on the transcript message so a reloaded session
// shows what was decided instead of offering the choice again.
export type PlanStatus = "proposed" | "approved" | "cancelled";

// One argument of one tool, as the backend derives it from that tool's own args model
// (`tools/registry.py::tool_specs`). The editor renders itself from these, so it can
// never offer a knob the tool does not have or a range it would reject.
export interface ToolArgSpec {
  name: string;
  kind: "text" | "int" | "bool" | "enum" | "enum_list" | "text_list";
  options: string[];
  required: boolean;
  default: unknown;
  description: string;
  min?: number;
  max?: number;
}

export interface ToolSpec {
  name: string;
  description: string;
  args: ToolArgSpec[];
}

export interface ChatMessage {
  id: string;
  role: "user" | "agent";
  text: string;
  at: string;
  context?: ContextRef[];     // what this turn added to the loaded context
  run_id?: string;
  envelope?: Envelope;
  // The idea's stored state at the moment this envelope landed, frozen there so the
  // diff it renders survives a later accept overwriting the live record. `null` means
  // the run created the idea from nothing; absent on messages from before this field
  // existed, which fall back to a live lookup.
  baseline_idea?: IdeaRecordRaw | null;
  // Set on the agent message of a turn that stopped at its plan.
  plan?: AgentPlan;
  plan_status?: PlanStatus;
  plan_run_id?: string;
  /**
   * What each creative-thinker step of this turn was handed — the raw event payloads,
   * copied onto the transcript when the run landed. The live plan panel shows the same
   * thing from the event stream while a run is going; this is what keeps it readable
   * afterwards, which is when the question actually gets asked.
   */
  handovers?: unknown[];
  failed?: boolean;
  demo?: DemoKind;            // client-side demo turns (see SessionView)
  settings?: {
    agent?: "orchestrator" | "creative_thinker";
    mode?: string; ideaId?: string | null; width?: number; depth?: number;
    styles?: string[]; toolUse?: boolean;
  };
}

// The demo turns the composer can inject while the orchestrator is unbuilt.
export type DemoKind = "suggestions" | "push-question" | "idea-diff";

// A working copy: an unaccepted `idea.json` living in the session's own folder.
// Real record, real schema, not in the knowledge base — `_session` says where it
// came from and what accepting it would do.
export interface SessionDraft extends IdeaRecordRaw {
  _session: {
    draft_id: string;
    session_id: string;
    // "create" mints a new idea on accept; "update" overwrites `target_idea_id`.
    kind: "create" | "update";
    run_id: string;
    source_index: number | null;
    handle?: string;
    offer_run?: string;
    target_idea_id: string | null;
    // `target_idea_id`'s record as it stood when this copy was offered, frozen so a
    // diff against it survives an eventual accept overwriting that same record.
    // `null` for a "create" copy, which has no prior version to diff against.
    baseline_idea?: IdeaRecordRaw | null;
    label: string;
    // "open" until the user decides; then "accepted" or "dismissed" — the decision
    // is stored, so a reloaded transcript shows what was chosen rather than
    // offering the choice again.
    status: "open" | "accepted" | "dismissed" | string;
    result_idea_id?: string;
    decided_at?: string;
    created_at: string;
  };
}

export interface Session {
  session_id: string;
  category_slug: string;
  title: string;
  created_at: string;
  updated_at: string;
  messages: ChatMessage[];
  context: ContextRef[];
  touched: string[];
}

// The list-row form the sidebar renders (no transcript).
export interface SessionSummary {
  session_id: string;
  title: string;
  createdAt: string;
  updatedAt: string;
  messageCount: number;
  contextCount: number;
  touched: string[];
  preview: string;
}

// --- the creative thinker's result envelope -----------------------------------
// Mirrors src/agents/creative_thinker/schemas.py::Envelope. Always complete: a run
// that accepted nothing still returns a valid `idea` and an empty `drafts`.

export interface SuggestedOperation {
  type: "update-idea" | "create-child" | "create-variant" | "merge" | "investigate";
  node_id: string;
  diagnosis: string;
  rationale: string;
  payload: Record<string, unknown>;
  group: string;
}

export interface OpenQuestion {
  node_id: string;
  question: string;
  options: string[];
}

// The raw IdeaRecord shape as it comes back inside an envelope (snake_case — this
// is the agent's own schema, not the mapped view model).
export interface IdeaRecordRaw {
  idea_id: string | null;
  title: string;
  state: string;
  one_liner: string;
  description: string;
  problem: string;
  addresses: string[];
  sections: Record<string, unknown>;
  section_order?: string[];
  inherited_from?: { idea_id?: string; upto_run_id?: string } | null;
  origin?: Record<string, unknown> | null;
  runs?: unknown[];
  [k: string]: unknown;
}

export interface Envelope {
  idea: IdeaRecordRaw;
  answer: string;
  drafts: IdeaRecordRaw[];
  suggested_operations: SuggestedOperation[];
  questions: OpenQuestion[];
  status: "completed" | "stopped-uncertain" | "truncated";
  status_reason: string;
  limit: string | null;
  metrics: {
    llm_calls: number; investigations: number; tokens: number; duration_s: number;
    nodes: number; mean_pairwise_distance: number; cluster_count: number;
  };
  run_id: string;
}

// --- session workspace panels --------------------------------------------------
// The right-hand column: a stack of panels the user adds, removes and resizes.
// The chat is not one of them — it is always there, on the left.

// `plan` shows the orchestrator's plan for the session's current run: which subagent
// each step calls, with which arguments, and where the run has got to in it.
// `thoughts` shows the tree of thoughts of the session's current run — what the
// agent is *considering*, which is the thing worth a window; a stage list only
// says where it is.
// "chat" is a window like any other so it can be dragged, stacked and put side
// by side with the rest — it is only special in that it cannot be closed.
export type PanelKind = "chat" | "graph" | "context" | "plan" | "idea" | "thoughts";

export interface Panel {
  uid: string;
  kind: PanelKind;
  // `idea` panels pin one idea.
  ref?: string | null;
  // Flex weight within the column, so a drag resizes proportionally.
  weight: number;
}

/** A vertical stack of windows. A Row is a horizontal band of these, which is
 *  what lets windows sit side by side as well as above one another. */
export interface Column {
  uid: string;
  // Flex weight within the row — the same proportional scheme as Panel.weight.
  weight: number;
  panels: Panel[];
}

/** A horizontal band of columns. The session is a vertical stack of these, which
 *  is what lets a full-width window sit below a row of side-by-side windows. */
export interface Row {
  uid: string;
  // Flex weight among rows — the same proportional scheme as Panel.weight.
  weight: number;
  columns: Column[];
}

export type NavView =
  | "topic"
  | "papers"
  | "paper"
  | "gaps"
  | "gap"
  | "ideas"
  | "idea"
  | "idea-graph"
  | "sessions"
  | "session"
  | "new-idea";

export interface Nav {
  view: NavView;
  id: string | null;
}
