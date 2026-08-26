import type { RunEvent, RunState } from "./types";

export type OrchestratorStepStatus = "pending" | "active" | "done" | "failed";

export interface OrchestratorStepProgress {
  number: number;
  tool: string;
  title: string;
  why: string;
  status: OrchestratorStepStatus;
  error: string;
}

export interface OrchestratorProgressState {
  phase: string;
  goal: string;
  detail: string;
  steps: OrchestratorStepProgress[];
  updatedAt: string | null;
}

interface ParsedPlan {
  goal: string;
  steps: OrchestratorStepProgress[];
}

function object(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown> : null;
}

function string(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function number(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function unescapePython(value: string): string {
  return value.replace(/\\(['"\\])/g, "$1").replace(/\\n/g, " ");
}

function quotedAfter(raw: string, marker: string): string {
  const start = raw.indexOf(marker);
  if (start < 0) return "";
  let at = start + marker.length;
  while (/\s/.test(raw[at] || "")) at += 1;
  const quote = raw[at];
  if (quote !== "'" && quote !== '"') return "";
  let value = "";
  for (at += 1; at < raw.length; at += 1) {
    const char = raw[at];
    if (char === "\\" && at + 1 < raw.length) {
      value += char + raw[++at];
    } else if (char === quote) {
      return unescapePython(value);
    } else {
      value += char;
    }
  }
  return "";
}

function dictString(raw: string, key: string): string {
  for (const marker of [`'${key}':`, `"${key}":`]) {
    const value = quotedAfter(raw, marker);
    if (value) return value;
  }
  return "";
}

function stepTitle(tool: string, args: Record<string, unknown>, why: string): string {
  if (tool === "investigator") return string(args.query) || why || "Research the question";
  if (tool === "creative_thinker") {
    return string(args.instruction) || string(args.task) || why || "Generate candidate ideas";
  }
  return string(args.instruction) || string(args.query) || why || tool.split("_").join(" ");
}

function structuredPlan(value: unknown): ParsedPlan | null {
  const plan = object(value);
  if (!plan || !Array.isArray(plan.steps)) return null;
  const steps = plan.steps.map((raw, index) => {
    const step = object(raw) || {};
    const args = object(step.args) || {};
    const tool = string(step.tool) || "step";
    const why = string(step.why);
    return {
      number: index + 1, tool, title: stepTitle(tool, args, why), why,
      status: "pending" as const, error: "",
    };
  });
  return { goal: string(plan.goal), steps };
}

// Older run logs stringify Pydantic's Plan object. Reading that representation keeps
// an already-running turn debuggable; new runs also emit a structured plan event.
function legacyPlan(value: unknown): ParsedPlan | null {
  if (typeof value !== "string") return null;
  const starts = [...value.matchAll(/ToolCall\(tool=(['"])(.*?)\1/g)];
  if (!starts.length && !value.includes("steps=[]")) return null;
  const steps = starts.map((match, index) => {
    const from = match.index || 0;
    const to = starts[index + 1]?.index ?? value.length;
    const chunk = value.slice(from, to);
    const tool = match[2];
    const why = quotedAfter(chunk, "why=");
    const args = {
      query: dictString(chunk, "query"),
      instruction: dictString(chunk, "instruction"),
      task: dictString(chunk, "task"),
    };
    return {
      number: index + 1, tool, title: stepTitle(tool, args, why), why,
      status: "pending" as const, error: "",
    };
  });
  return { goal: quotedAfter(value, "goal="), steps };
}

function planFromEvent(event: RunEvent) {
  if (event.kind === "event" && event.name === "orchestrator plan") {
    return structuredPlan(event.data);
  }
  if (event.kind !== "node" || event.node !== "plan" || event.ns) return null;
  const delta = object(event.result);
  return structuredPlan(delta?.plan) || legacyPlan(delta?.plan);
}

function labelNode(node: string): string {
  return node.split("_").join(" ").replace(/\b\w/g, (char: string) => char.toUpperCase());
}

function investigatorDetail(event: RunEvent): string {
  const result = object(event.result);
  if (event.node === "actor") {
    const mode = string(result?.mode);
    if (mode === "paper_search") return "Searching for relevant papers";
    if (mode === "paper") return "Retrieving and analysing the paper";
    if (mode === "web") return "Searching the web";
    if (mode === "url") return "Reading the supplied URL";
  }
  if (event.node === "paper_search") return "Downloading the selected papers";
  if (event.node === "download_papers") return "Checking the research results";
  if (event.node === "evaluate") return "Writing up the findings";
  if (event.node === "reflect") return "Revising the research approach";
  if (event.node === "render") return "Research complete; starting the next step";
  return event.node ? `Investigator · ${labelNode(event.node)}` : "Investigator running";
}

function latestDetail(events: RunEvent[]): string {
  const last = events[events.length - 1];
  if (!last) return "Loading the selected context";
  if (last.kind === "event") {
    if (last.name === "orchestrator planning") return "Choosing the agents and their order";
    if (last.name === "orchestrator plan") return "The plan is ready; starting its first step";
    if (last.name === "orchestrator step started") {
      const tool = string(last.data?.tool).split("_").join(" ");
      const step = number(last.data?.step);
      return `${tool || "Agent"} · running${step ? ` step ${step}` : ""}`;
    }
    if (last.name === "orchestrator step finished") {
      const step = number(last.data?.step);
      return last.data?.ok === false
        ? `Step ${step || ""} failed; deciding what can continue`
        : `Step ${step || ""} complete; starting the next step`;
    }
    if (last.name === "orchestrator review started") return "Checking the results against the plan";
    if (last.name === "orchestrator review finished") return "Review complete";
    if (last.name === "investigator downloads started") {
      const count = number(last.data?.count);
      return count ? `Investigator · downloading ${count} selected papers` : "Investigator · downloading papers";
    }
    if (last.name === "investigator download progress") {
      const completed = number(last.data?.completed);
      const total = number(last.data?.total);
      return completed !== null && total !== null
        ? `Investigator · downloaded ${completed} of ${total} papers`
        : "Investigator · downloading papers";
    }
    if (last.name === "investigator downloads finished") return "Investigator · checking the downloaded papers";
    if (last.name?.startsWith("branch ") || last.name === "run prepared") {
      return "Creative thinker · developing candidate branches";
    }
  }
  if (last.ns?.includes("/")) {
    return `Creative thinker · ${labelNode(last.node || "working")}`;
  }
  if (last.ns?.startsWith("execute:")) return investigatorDetail(last);
  if (last.node === "execute") return "Checking the results against the plan";
  if (last.node === "reflect") return "Review complete";
  if (last.node === "finish" || last.kind === "run_end") return "Finished";
  if (last.node === "assemble_context") return "Choosing the agents and their order";
  return last.node ? labelNode(last.node) : "Working";
}

export function isOrchestratorRun(run: RunState): boolean {
  return run.agent === "orchestrator_agent"
    || run.events.some((event) => event.kind === "run_start" && event.agent === "orchestrator_agent")
    || run.events.some((event) => event.kind === "node" && !event.ns && event.node === "plan");
}

export function progressForRun(run: RunState): OrchestratorProgressState {
  let lastPlanIndex = -1;
  let parsed: ParsedPlan | null = null;
  for (let index = 0; index < run.events.length; index += 1) {
    const event = run.events[index];
    const candidate = planFromEvent(event);
    if (candidate && (event.name === "orchestrator plan" || parsed === null)) {
      parsed = candidate;
      lastPlanIndex = index;
    }
  }

  const steps = (parsed?.steps || []).map((step) => ({ ...step }));
  const events = lastPlanIndex >= 0 ? run.events.slice(lastPlanIndex + 1) : run.events;
  let sawExplicitLifecycle = false;

  for (const event of events) {
    if (event.kind !== "event") continue;
    const stepNumber = number(event.data?.step);
    if (event.name === "orchestrator step started" && stepNumber) {
      sawExplicitLifecycle = true;
      steps.forEach((step) => {
        if (step.number < stepNumber && step.status === "pending") step.status = "done";
        if (step.number === stepNumber) step.status = "active";
      });
    }
    if (event.name === "orchestrator step finished" && stepNumber) {
      sawExplicitLifecycle = true;
      const step = steps.find((candidate) => candidate.number === stepNumber);
      if (step) {
        step.status = event.data?.ok === false ? "failed" : "done";
        step.error = string(event.data?.error);
      }
    }
  }

  const executeDone = events.some((event) => event.kind === "node" && !event.ns && event.node === "execute");
  if (executeDone) {
    steps.forEach((step) => { if (step.status !== "failed") step.status = "done"; });
  } else if (!sawExplicitLifecycle && steps.length) {
    const creativeActive = events.some((event) => event.ns?.includes("/")
      || (event.kind === "event" && (event.name?.startsWith("branch ") || event.name === "run prepared")));
    let active = creativeActive ? steps.findIndex((step) => step.tool === "creative_thinker") : -1;
    if (active < 0 && events.some((event) => event.ns?.startsWith("execute:"))) {
      active = steps.findIndex((step) => step.tool === "investigator");
    }
    if (active < 0) active = 0;
    steps.forEach((step, index) => {
      step.status = index < active ? "done" : index === active ? "active" : "pending";
    });
  }

  const finished = run.events.some((event) => event.kind === "run_end") || run.status === "done";
  const reviewing = executeDone && !finished;
  const failed = run.status === "error" || run.status === "cancelled" || run.status === "interrupted";
  const phase = failed ? (run.status === "error" ? "Run failed" : labelNode(run.status))
    : finished ? "Complete"
    : !parsed ? "Planning"
    : reviewing ? "Reviewing results"
    : "Running plan";
  const updatedAt = run.events.length ? run.events[run.events.length - 1].t : null;

  return {
    phase,
    goal: parsed?.goal || "",
    detail: failed ? (run.error || latestDetail(events)) : latestDetail(events),
    steps,
    updatedAt,
  };
}
