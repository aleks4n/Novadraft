// The orchestrator's plan, before it runs.
//
// A planned turn stops once it knows what it would do: which subagent it would call,
// in which order, and with which arguments. That is what this renders — and, while the
// plan is still only proposed, what it lets the user change. Nothing is dispatched
// until Start is pressed; pressing it runs a second run over the same turn carrying
// the plan exactly as it is on screen.
//
// The fields are generated from the backend's tool specs, which are themselves derived
// from each tool's own argument model. So the editor cannot offer a knob the tool does
// not have, or a range the tool would reject — a creative thinker's width stops at the
// number of distinct stances there are, because that is where the schema stops it.
import { useEffect, useMemo, useState } from "react";
import { Icon } from "./Icon";
import { agentApi } from "../lib/api";
import type { AgentPlan, PlanStep, ToolArgSpec, ToolSpec } from "../lib/types";

// One fetch per page: the catalogue changes when the backend changes, not while the
// user is reading a plan.
let specsPromise: Promise<ToolSpec[]> | null = null;

export function useToolSpecs(): ToolSpec[] {
  const [specs, setSpecs] = useState<ToolSpec[]>([]);
  useEffect(() => {
    let live = true;
    specsPromise = specsPromise ?? agentApi.orchestratorTools();
    specsPromise
      .then((s) => { if (live) setSpecs(s); })
      .catch(() => { specsPromise = null; });
    return () => { live = false; };
  }, []);
  return specs;
}

export const agentLabel = (tool: string) => tool.split("_").join(" ");

/** The one line that says what a step is for. */
function stepTitle(step: PlanStep): string {
  const args = step.args || {};
  const first = ["instruction", "query", "task"]
    .map((key) => (typeof args[key] === "string" ? (args[key] as string) : ""))
    .find(Boolean);
  return first || step.why || agentLabel(step.tool);
}

/** Is this value worth putting on screen? An empty string, an empty list and a false
 *  flag all say "this argument was not used", which the chips do not need to say. */
function meaningful(value: unknown): boolean {
  if (Array.isArray(value)) return value.length > 0;
  if (typeof value === "string") return value.trim().length > 0;
  if (typeof value === "boolean") return value;
  return value !== null && value !== undefined;
}

function chipValue(value: unknown): string {
  if (Array.isArray(value)) return value.join(", ");
  if (typeof value === "boolean") return "on";
  return String(value);
}

// The arguments that read as prose belong in the step's title, not repeated as a chip.
const TITLE_ARGS = new Set(["instruction", "query", "task"]);

function relevantArg(step: PlanStep, name: string): boolean {
  if (step.tool !== "creative_thinker") return true;
  const mode = step.args?.thinking_mode ?? "generation";
  // `width` is the branching factor of one expansion in *either* mode — in refinement
  // it is how many branches a level opens before the evaluator picks one. The single
  // case where it says nothing is a depth-1 refinement, which the call schema pins to
  // one branch because that is the promised plain chain of thought. `depth` is the one
  // that is refinement-only: generation is depth 1 by definition.
  if (name === "depth") return mode === "refinement";
  if (name !== "width") return true;
  const depth = step.args?.depth;
  return mode !== "refinement" || (typeof depth === "number" ? depth : 3) > 1;
}

/** Every argument of one step as a row of chips — the "which parameters" half. */
export function StepArgs({ step, specs }: { step: PlanStep; specs: ToolSpec[] }) {
  const spec = specs.find((s) => s.name === step.tool);
  const order = spec ? spec.args.map((a) => a.name) : Object.keys(step.args || {});
  const shown = order
    .filter((name) => !TITLE_ARGS.has(name) && relevantArg(step, name)
      && meaningful((step.args || {})[name]));
  if (!shown.length && !step.needs?.length) return null;
  return (
    <div className="plan-args">
      {step.needs?.length > 0 && (
        <span className="plan-arg plan-arg--needs" title="Works on what these steps produced">
          <span className="plan-arg-key">on step</span>
          <span className="plan-arg-val">{step.needs.join(", ")}</span>
        </span>
      )}
      {shown.map((name) => (
        <span className="plan-arg" key={name}
          title={spec?.args.find((a) => a.name === name)?.description || name}>
          <span className="plan-arg-key">{name.split("_").join(" ")}</span>
          <span className="plan-arg-val">{chipValue((step.args || {})[name])}</span>
        </span>
      ))}
    </div>
  );
}

// --- editing one argument -------------------------------------------------------

function ArgField({
  spec, value, onChange,
}: { spec: ToolArgSpec; value: unknown; onChange: (v: unknown) => void }) {
  const label = spec.name.split("_").join(" ");
  const list = Array.isArray(value) ? (value as string[]) : [];

  return (
    <label className="plan-field" title={spec.description}>
      <span className="plan-field-label">
        {label}
        {spec.kind === "int" && spec.min !== undefined && spec.max !== undefined && (
          <span className="plan-field-range">{spec.min}–{spec.max}</span>
        )}
      </span>

      {spec.kind === "int" ? (
        <input
          className="plan-field-input" type="number"
          min={spec.min} max={spec.max}
          value={typeof value === "number" ? value : Number(spec.default ?? 0)}
          onChange={(e) => {
            const raw = Number(e.target.value);
            if (Number.isNaN(raw)) return;
            const low = spec.min ?? raw;
            const high = spec.max ?? raw;
            onChange(Math.max(low, Math.min(high, raw)));
          }}
        />
      ) : spec.kind === "bool" ? (
        <input className="plan-field-check" type="checkbox" checked={value === true}
          onChange={(e) => onChange(e.target.checked)} />
      ) : spec.kind === "enum" ? (
        <select className="plan-field-input" value={String(value ?? spec.default ?? "")}
          onChange={(e) => onChange(e.target.value)}>
          {spec.options.map((o) => <option key={o} value={o}>{o}</option>)}
        </select>
      ) : spec.kind === "enum_list" ? (
        <span className="plan-field-chips">
          {spec.options.map((option) => {
            const on = list.includes(option);
            return (
              <button
                type="button" key={option}
                className={"plan-chip" + (on ? " plan-chip--on" : "")}
                onClick={() => onChange(on ? list.filter((v) => v !== option)
                  : [...list, option])}
              >
                {option}
              </button>
            );
          })}
        </span>
      ) : spec.kind === "text_list" ? (
        <input
          className="plan-field-input" type="text" value={list.join(", ")}
          placeholder="comma separated"
          onChange={(e) => onChange(
            e.target.value.split(",").map((v) => v.trim()).filter(Boolean))}
        />
      ) : (
        <textarea
          className="plan-field-input plan-field-input--area" rows={2}
          value={typeof value === "string" ? value : ""}
          onChange={(e) => onChange(e.target.value)}
        />
      )}
    </label>
  );
}

// --- the card -------------------------------------------------------------------

/** Drop one step, keeping every `needs` reference pointing where it pointed.
 *
 *  Step numbers are positional, so removing the second step turns the third into the
 *  second — and a later step that said "work on what step 3 produced" would then be
 *  working on something else entirely. A reference to the step that went away is
 *  dropped rather than repointed: nothing produces that output any more. */
function withoutStep(steps: PlanStep[], index: number): PlanStep[] {
  return steps
    .filter((_, i) => i !== index)
    .map((step) => ({
      ...step,
      needs: (step.needs || [])
        .filter((n) => n !== index + 1)
        .map((n) => (n > index + 1 ? n - 1 : n)),
    }));
}

const COUNT_CRITERION = /^\s*\d+\s+(?:further\s+)?candidates?\s+returned\s*$/i;

function criteriaForWidth(criteria: string[], width: number): string[] {
  return [
    `${width} candidate${width === 1 ? "" : "s"} returned`,
    ...criteria.filter((criterion) => !COUNT_CRITERION.test(criterion)),
  ];
}

/** Widening a refinement widens the *tree*, not the return: however many branches a
 *  level opens, one revised idea comes back. A criterion counting candidates can
 *  therefore never be met by a refinement-only plan, and reflect reads an unmeetable
 *  criterion as a shortfall and replans, running the whole tree of thought again. So
 *  drop the count rather than restating it — the width itself is left alone. */
function criteriaForRefinement(criteria: string[]): string[] {
  const kept = criteria.filter((criterion) => !COUNT_CRITERION.test(criterion));
  return kept.length ? kept : ["at least one revised candidate returned"];
}

export function PlanReview({
  plan, status, busy, onStart, onCancel,
}: {
  plan: AgentPlan;
  status: "proposed" | "approved" | "cancelled";
  busy?: boolean;
  onStart: (plan: AgentPlan) => void;
  onCancel: () => void;
}) {
  const specs = useToolSpecs();
  const [draft, setDraft] = useState<AgentPlan>(plan);
  const [open, setOpen] = useState<number | null>(null);
  // A plan that has been started is a record of what ran, so it follows the stored one
  // rather than whatever the editor had in it.
  useEffect(() => { setDraft(plan); }, [plan]);

  // Planner output is free-form until tool validation. Clamp any integer it supplied
  // as soon as the catalogue arrives, so an untouched out-of-range value cannot make
  // Start fail.
  useEffect(() => {
    if (!specs.length || status !== "proposed") return;
    setDraft((cur) => ({
      ...cur,
      steps: cur.steps.map((step) => {
        const spec = specs.find((item) => item.name === step.tool);
        if (!spec) return step;
        const args = { ...step.args };
        spec.args.filter((arg) => arg.kind === "int").forEach((arg) => {
          if (typeof args[arg.name] !== "number") return;
          const low = arg.min ?? (args[arg.name] as number);
          const high = arg.max ?? (args[arg.name] as number);
          args[arg.name] = Math.max(low, Math.min(high, args[arg.name] as number));
        });
        return { ...step, args };
      }),
    }));
  }, [specs, status]);

  const editable = status === "proposed";
  const steps = editable ? draft.steps : plan.steps;

  const setStep = (index: number, patch: Partial<PlanStep>) =>
    setDraft((cur) => {
      const steps = cur.steps.map((step, i) => (i === index ? { ...step, ...patch } : step));
      // An edited width is authoritative over the count the planner wrote into the
      // criteria, so the two never disagree on the card — but only where width is a
      // count of what comes back. In refinement it is the branching factor of the tree
      // and one revised idea is returned regardless, so a count taken from it would be a
      // criterion the plan cannot meet.
      const edited = steps[index];
      const isThinker = edited.tool === "creative_thinker";
      const generation = (edited.args?.thinking_mode ?? "generation") === "generation";
      const width = isThinker && generation ? edited.args?.width : undefined;
      const criteria = cur.done_criteria || [];
      const countsElsewhere = steps.some(
        (step, i) => i !== index && step.tool === "creative_thinker"
          && (step.args?.thinking_mode ?? "generation") === "generation");
      return {
        ...cur,
        steps,
        done_criteria:
          typeof width === "number" && width !== cur.steps[index].args?.width
            ? criteriaForWidth(criteria, width)
            : isThinker && !generation && !countsElsewhere
              ? criteriaForRefinement(criteria)
              : criteria,
      };
    });

  const specFor = useMemo(
    () => (tool: string) => specs.find((s) => s.name === tool) || null, [specs]);

  return (
    <div className={"plan-card plan-card--" + status}>
      <div className="plan-card-head">
        <Icon name="sparkle" size={13} />
        <span className="plan-card-kind">
          {status === "proposed" ? "Plan — waiting for you"
            : status === "approved" ? "Plan — started" : "Plan — not run"}
        </span>
        <span className="plan-card-count">
          {steps.length} step{steps.length === 1 ? "" : "s"}
        </span>
      </div>

      {draft.goal && <p className="plan-goal">{draft.goal}</p>}

      <ol className="plan-steps">
        {steps.map((step, index) => {
          const spec = specFor(step.tool);
          const expanded = open === index;
          return (
            <li className="plan-step" key={index}>
              <span className="plan-step-num">{index + 1}</span>
              <div className="plan-step-body">
                <div className="plan-step-head">
                  <span className="plan-step-agent" title={spec?.description || step.tool}>
                    {agentLabel(step.tool)}
                  </span>
                  {editable && (
                    <>
                      <button className="ghost-btn plan-step-edit"
                        onClick={() => setOpen(expanded ? null : index)}
                        title="Change what this agent is called with">
                        <Icon name={expanded ? "chevron-down" : "settings"} size={13} />
                        {expanded ? "done" : "adjust"}
                      </button>
                      <button className="ghost-btn plan-step-drop"
                        onClick={() => {
                          setDraft((cur) => ({ ...cur, steps: withoutStep(cur.steps, index) }));
                          setOpen(null);
                        }}
                        title="Drop this step from the plan">
                        <Icon name="x" size={12} />
                      </button>
                    </>
                  )}
                </div>
                <div className="plan-step-title">{stepTitle(step)}</div>
                {step.why && step.why !== stepTitle(step) && (
                  <div className="plan-step-why">{step.why}</div>
                )}
                <StepArgs step={step} specs={specs} />

                {editable && expanded && (
                  <div className="plan-fields">
                    {spec ? spec.args.map((arg) => (
                      <ArgField
                        key={arg.name} spec={arg}
                        value={(step.args || {})[arg.name] ?? arg.default}
                        onChange={(v) => setStep(index, { args: { ...step.args, [arg.name]: v } })}
                      />
                    )) : (
                      <p className="plan-fields-empty">
                        This step calls <b>{step.tool}</b>, which the backend does not list
                        as a tool — its arguments cannot be edited here.
                      </p>
                    )}
                  </div>
                )}
              </div>
            </li>
          );
        })}
      </ol>

      {(editable ? draft : plan).done_criteria?.length > 0 && (
        <div className="plan-criteria">
          <span className="plan-criteria-label">Done when</span>
          <ul>{(editable ? draft : plan).done_criteria.map(
            (c, i) => <li key={i}>{c}</li>)}</ul>
        </div>
      )}

      {status === "proposed" && (
        <div className="plan-actions">
          <button className="btn btn--primary" disabled={busy || !steps.length}
            onClick={() => onStart(draft)}>
            <Icon name={busy ? "clock" : "arrow-right"} size={13} />
            {busy ? "Starting…" : "Start"}
          </button>
          <button className="btn btn--ghost" disabled={busy} onClick={onCancel}>
            Discard
          </button>
          <span className="plan-actions-note">
            Nothing runs until you start it — adjust any step first.
          </span>
        </div>
      )}
      {status === "cancelled" && (
        <div className="plan-actions-note">You turned this plan down; nothing ran.</div>
      )}
    </div>
  );
}
