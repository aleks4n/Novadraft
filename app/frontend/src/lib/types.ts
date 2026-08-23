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

export interface Idea {
  id: string;
  title: string;
  short: string;
  description: string;
  summary: string;
  status: string;
  source: string; // "agent" | "user"
  novelty: number | null;
  feasibility: number | null;
  impact: number | null;
  relatedGaps: string[];
  relatedPapers: string[];
  method: string[];
  evaluation: IdeaEvaluation;
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
  kind: "paper" | "idea" | "resume";
  status: RunStatus;
  query: string | null;
  events: RunEvent[];
  error: string | null;
  resultPaperId: string | null;
  resultIdeaIds: string[];
  candidates: PaperCandidate[];
  target?: { kind: "paper" | "gap"; id: string } | null;
  note?: string | null;
}

export type NavView =
  | "topic"
  | "papers"
  | "paper"
  | "gaps"
  | "gap"
  | "ideas"
  | "idea"
  | "new-idea";

export interface Nav {
  view: NavView;
  id: string | null;
}
