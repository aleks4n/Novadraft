"""Eval harness for the orchestrator's planner.

Runs ONLY the plan node over a set of fixture turns — no subagents, no execute, no
reflect — and checks that each plan is *functionally* well-formed: the tool exists, its
args validate, the thinking mode matches what was actually focused, and a turn that needs
no tools asks for none.

These are the rules `subagents/plan/prompt.md` is supposed to enforce. Nothing verified
them before, so the only way to know the prompt still worked was to eyeball `demo.py`.

One LLM call per case (~5s, cheap model), so a full pass is well under a minute. Run it
before and after a prompt change with different labels and compare the reports.

Usage:
    # measure. --label says what the run is testing; --repeat averages out the noise
    python -m dev.eval_orchestrator_plans --label baseline --repeat 3

    # change a prompt, measure again, then see only what moved
    python -m dev.eval_orchestrator_plans --label plan_answer --repeat 3
    python -m dev.eval_orchestrator_plans --compare baseline plan_answer

    # a couple of cases while iterating
    python -m dev.eval_orchestrator_plans --only chat_greeting,gap_generate --label quick
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import _env_bootstrap  # noqa: F401,E402

# Importing the agent pulls in langgraph + langchain, which is slow from cold (a minute
# or more). Say so before it happens, or the harness looks wedged.
print("loading the orchestrator (langgraph import — slow from cold) ...", flush=True)

from src.common.schemas.gap import GapRecord  # noqa: E402
from src.common.utils import storage  # noqa: E402
from src.agents.orchestrator_agent.context import assemble_context  # noqa: E402
from src.agents.orchestrator_agent.schemas import FocusSelection, Plan  # noqa: E402
from src.agents.orchestrator_agent.subagents.plan.agent import plan as plan_node  # noqa: E402
from src.agents.orchestrator_agent.tools.registry import TOOLS  # noqa: E402

EVAL_DIR = Path(__file__).resolve().parent / "eval_runs"

# A case is retried only when the attempt itself failed (dropped connection, or the model
# never produced parseable structured output). A plan that returns and fails the checks is
# the measurement, not an error, and is never retried.
_CASE_ATTEMPTS = 3
_BACKOFF = 4.0  # seconds, multiplied by the attempt number

# Tools whose results carry ideas, so a `needs` pointing at them has something to pass on.
# Mirrors what `agent._context_for` does at run time; update when a tool starts producing.
_PRODUCES_IDEAS = {"creative_thinker"}


# --- fixtures -----------------------------------------------------------------
# `focus` is resolved at run time: "gap" -> a real gap id from the category,
# "idea" -> a real idea id, None -> nothing selected.

CASES: list[dict] = [
    # --- turns that should call no tool at all ---
    {"name": "chat_greeting", "focus": None, "message": "hi",
     "expect": {"steps": 0}},
    {"name": "chat_capabilities", "focus": None, "message": "what can you do?",
     "expect": {"steps": 0}},
    {"name": "chat_thanks", "focus": None, "message": "thanks, that's helpful",
     "expect": {"steps": 0}},

    # --- generation from a gap ---
    {"name": "gap_generate", "focus": "gap", "message": "Give me 3 ideas for this gap",
     "expect": {"tool": "creative_thinker", "thinking_mode": "generation", "width": 3}},
    {"name": "gap_generate_constrained", "focus": "gap",
     "message": "Give me 3 ideas for this gap, nothing that needs retraining",
     "expect": {"tool": "creative_thinker", "thinking_mode": "generation"}},
    {"name": "gap_generate_implicit", "focus": "gap", "message": "what could we do here?",
     "expect": {"tool": "creative_thinker", "thinking_mode": "generation"}},

    # --- a plan whose second step consumes the first. Without `needs`, step 2 runs
    #     against the up-front context, finds no ideas, and produces nothing. ---
    {"name": "gap_generate_then_refine", "focus": "gap",
     "message": "give me a few ideas for this gap, then sharpen whatever you come up with",
     "expect": {"tool": "creative_thinker", "chained": True}},

    # --- refinement of an existing idea ---
    {"name": "idea_refine", "focus": "idea", "message": "make this idea sharper",
     "expect": {"tool": "creative_thinker", "thinking_mode": "refinement"}},
    {"name": "idea_refine_specific", "focus": "idea",
     "message": "the evaluation plan is vague — make it concrete",
     "expect": {"tool": "creative_thinker", "thinking_mode": "refinement"}},
    {"name": "idea_critique", "focus": "idea", "message": "what's the weakest part of this?",
     "expect": {"tool": "creative_thinker", "thinking_mode": "refinement"}},

    # --- research questions ---
    {"name": "research_question", "focus": None,
     "message": "has anyone published on learned attention compatibility functions?",
     "expect": {"tool": "investigator"}},
    {"name": "research_on_gap", "focus": "gap",
     "message": "find me papers that already tried to solve this",
     "expect": {"tool": "investigator"}},

    # --- ambiguous: no focus but asks for ideas. Any plan is acceptable as long as
    #     it is well-formed; this case exists to catch crashes and bad args. ---
    {"name": "ideas_without_focus", "focus": None, "message": "give me some ideas",
     "expect": {}},
]


# --- checks -------------------------------------------------------------------


def check(plan: Plan, expect: dict, focus: FocusSelection) -> list[str]:
    """Structural failures for one plan. Empty list = pass."""
    fails: list[str] = []

    # Always: every step must be executable.
    for i, step in enumerate(plan.steps, 1):
        tool = TOOLS.get(step.tool)
        if tool is None:
            fails.append(f"step {i}: unknown tool {step.tool!r}")
            continue
        try:
            tool.args_schema.model_validate(step.args)
        except Exception as exc:  # noqa: BLE001
            fails.append(f"step {i}: args rejected by {step.tool} ({type(exc).__name__})")

    # Always: steps without criteria leave reflect nothing to judge.
    if plan.steps and not plan.done_criteria:
        fails.append("steps planned but done_criteria is empty")
    if not plan.goal.strip():
        fails.append("goal is empty")

    # Always: a plan with no steps skips execute and reflect, so `answer` is the only
    # thing the user will get. Empty here means an empty chat bubble.
    if not plan.steps and not plan.answer.strip():
        fails.append("no steps and no answer — the user would get nothing")

    # Always: `needs` must point backwards at a step that can actually supply ideas.
    # A forward or self reference is skipped at run time, so it is a dead step.
    for i, step in enumerate(plan.steps, 1):
        for n in step.needs:
            if not 1 <= n < i:
                fails.append(f"step {i}: needs step {n}, which is not an earlier step")
            elif plan.steps[n - 1].tool not in _PRODUCES_IDEAS:
                fails.append(f"step {i}: needs step {n} ({plan.steps[n - 1].tool}), "
                             "which returns no ideas")

    # The mode has to match what is in context when the step runs: refinement needs an
    # idea, which comes either from the user's selection or from an earlier step.
    for i, step in enumerate(plan.steps, 1):
        if step.tool != "creative_thinker":
            continue
        mode = step.args.get("thinking_mode")
        if mode == "refinement" and not focus.idea_ids and not step.needs:
            fails.append(f"step {i}: refinement planned with no idea in focus and no needs")
        if mode == "generation" and not (focus.gap_ids or focus.idea_ids):
            fails.append(f"step {i}: generation planned with nothing in focus")

    # Per-case expectations.
    if "steps" in expect and len(plan.steps) != expect["steps"]:
        fails.append(f"expected {expect['steps']} step(s), got {len(plan.steps)}")
    if "tool" in expect:
        used = [s.tool for s in plan.steps]
        if expect["tool"] not in used:
            fails.append(f"expected tool {expect['tool']!r}, got {used or 'none'}")
    if "width" in expect:
        # A count the user said out loud should reach the tool as a number, not just as
        # prose inside `instruction` where the thinker may or may not honour it.
        widths = [s.args.get("width") for s in plan.steps if s.tool == "creative_thinker"]
        if expect["width"] not in widths:
            fails.append(f"expected width {expect['width']}, got {widths or 'none'}")
    if expect.get("chained") and not any(s.needs for s in plan.steps):
        fails.append("expected a step to consume an earlier one via needs, none did")
    if "thinking_mode" in expect:
        modes = [s.args.get("thinking_mode") for s in plan.steps
                 if s.tool == "creative_thinker"]
        if expect["thinking_mode"] not in modes:
            fails.append(f"expected mode {expect['thinking_mode']!r}, got {modes or 'none'}")
    return fails


# --- fixture resolution -------------------------------------------------------


def _a_gap(category: str) -> str | None:
    for stage in ("master", "to_rank", "pending"):
        for path in sorted(storage.gap_stage_dir(category, stage).glob("*.json")):
            _s, raw = storage.find_gap(category, path.stem)
            try:
                GapRecord.model_validate(raw)
                return path.stem
            except Exception:  # noqa: BLE001
                continue
    return None


def _an_idea(category: str) -> str | None:
    ids = storage.list_idea_ids(category)
    return ids[0] if ids else None


# --- run ----------------------------------------------------------------------


def run_case(case: dict, category: str, gap_id: str | None, idea_id: str | None,
             log_error) -> dict:
    """One fixture turn -> a result row.

    Retries the whole attempt on *infrastructure* failure — a dropped connection, or the
    model answering in prose so often that the registry's own re-asks run out. Those say
    nothing about the prompt, and letting one poison a 12-case run wastes minutes of API
    calls. A plan that comes back and simply fails the checks is NOT retried: that's the
    signal we're here to measure.

    Errors are recorded per case (`error`) and never raised, so one bad case can't end
    the run; full tracebacks go to errors.log.
    """
    kind = case["focus"]
    if kind == "gap":
        focus = FocusSelection(gap_ids=[gap_id]) if gap_id else None
    elif kind == "idea":
        focus = FocusSelection(idea_ids=[idea_id]) if idea_id else None
    else:
        focus = FocusSelection()

    if focus is None:
        return {"name": case["name"], "skipped": f"no {kind} available in '{category}'"}

    t0 = time.time()
    last = ""
    for attempt in range(1, _CASE_ATTEMPTS + 1):
        try:
            state = {"category_slug": category, "session_id": "session_eval",
                     "run_id": "run_eval", "message": case["message"], "focus": focus,
                     "iteration": 0}
            state.update(assemble_context(state))  # inside the try: it reads the disk
            plan: Plan = plan_node(state)["plan"]
        except Exception as exc:  # noqa: BLE001 — infrastructure, not a verdict
            last = f"{type(exc).__name__}: {exc}"
            log_error(case["name"], attempt, exc)
            if attempt < _CASE_ATTEMPTS:
                time.sleep(_BACKOFF * attempt)
                continue
            return {"name": case["name"], "message": case["message"], "focus": kind,
                    "seconds": round(time.time() - t0, 1), "attempts": attempt,
                    "fails": [], "error": last, "plan": None}

        return {
            "name": case["name"],
            "message": case["message"],
            "focus": kind,
            "seconds": round(time.time() - t0, 1),
            "attempts": attempt,
            "fails": check(plan, case["expect"], focus),
            "error": "",
            "plan": plan.model_dump(mode="json"),
        }


def _verdict(row: dict) -> str:
    """One token summarising a case, used in the table and by --compare.

    With --repeat this is a rate ("2/3"), so a case that only fails sometimes reads as
    flaky rather than as a clean pass or a clean failure.
    """
    if "skipped" in row:
        return "skip"
    n = row.get("n", 1)
    if n > 1:
        return f"{row['passes']}/{n}"
    if row.get("error"):
        return "ERROR"
    return "pass" if not row["fails"] else "FAIL"


def aggregate(name: str, message: str, focus: str, runs: list[dict]) -> dict:
    """Collapse repeated runs of one case into a single row.

    Keeps every distinct failure reason seen across repeats — a case that fails two
    different ways is more interesting than one that fails the same way twice.
    """
    passes = sum(1 for r in runs if not r["fails"] and not r.get("error"))
    fails: list[str] = []
    for r in runs:
        for f in r["fails"]:
            if f not in fails:
                fails.append(f)
    secs = sorted(r["seconds"] for r in runs)
    return {
        "name": name,
        "message": message,
        "focus": focus,
        "n": len(runs),
        "passes": passes,
        "fails": fails,
        "error": next((r["error"] for r in runs if r.get("error")), ""),
        "errors": sum(1 for r in runs if r.get("error")),
        "seconds": secs[len(secs) // 2],
        "attempts": max(r.get("attempts", 1) for r in runs),
        # The last plan that came back, for the appendix.
        "plan": next((r["plan"] for r in reversed(runs) if r.get("plan")), None),
    }


def compare(label_a: str, label_b: str) -> int:
    """Show only the cases whose verdict changed between two labelled runs."""
    def latest(label: str) -> Path | None:
        hits = sorted(EVAL_DIR.glob(f"*_plan_{label}"))
        return hits[-1] if hits else None

    dir_a, dir_b = latest(label_a), latest(label_b)
    for label, path in ((label_a, dir_a), (label_b, dir_b)):
        if path is None:
            print(f"no run found with label {label!r} in {EVAL_DIR}")
            return 1

    a = {r["name"]: r for r in json.loads((dir_a / "raw.json").read_text())}
    b = {r["name"]: r for r in json.loads((dir_b / "raw.json").read_text())}

    print(f"{dir_a.name}\n  → {dir_b.name}\n")
    width = max((len(n) for n in set(a) | set(b)), default=10)
    changed = 0
    for name in sorted(set(a) | set(b)):
        va = _verdict(a[name]) if name in a else "—"
        vb = _verdict(b[name]) if name in b else "—"
        if va == vb:
            continue
        changed += 1
        why = "; ".join((b.get(name) or {}).get("fails") or []) or \
              (b.get(name) or {}).get("error", "")
        print(f"  {name:<{width}}  {va:>5} → {vb:<5}  {why[:80]}")
    unchanged = len(set(a) & set(b)) - changed
    print(f"\n{changed} changed · {unchanged} unchanged")
    return 0


def render_markdown(label: str, category: str, results: list[dict]) -> str:
    ran = [r for r in results if "skipped" not in r]
    total_runs = sum(r.get("n", 1) for r in ran)
    total_passes = sum(r.get("passes", 0 if r.get("fails") or r.get("error") else 1)
                       for r in ran)
    errored = [r for r in ran if r.get("error")]
    skipped = [r for r in results if "skipped" in r]
    reps = max((r.get("n", 1) for r in ran), default=1)
    lines = [
        f"# Orchestrator plan eval — {label}",
        "",
        f"- category: `{category}`" + (f" · {reps} repeat(s) per case" if reps > 1 else ""),
        f"- **{total_passes}/{total_runs} passed**"
        + (f" · {len(errored)} case(s) errored" if errored else "")
        + (f" · {len(skipped)} skipped" if skipped else ""),
        "",
        "| case | focus | steps | tool | mode | s | try | result |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        if "skipped" in r:
            lines.append(f"| {r['name']} | — | — | — | — | — | — | skipped: {r['skipped']} |")
            continue
        p = r["plan"] or {"steps": []}
        steps = p["steps"]
        tools = ", ".join(s["tool"] for s in steps) or "—"
        modes = ", ".join(str(s["args"].get("thinking_mode", "")) for s in steps
                          if s["tool"] == "creative_thinker") or "—"
        verdict = _verdict(r)
        detail = "; ".join(r["fails"]) or r.get("error", "")
        if detail:
            verdict = f"{verdict}: {detail}"
        lines.append(f"| {r['name']} | {r['focus']} | {len(steps)} | {tools} | {modes} "
                     f"| {r['seconds']} | {r.get('attempts', 1)} | {verdict} |")

    lines += ["", "## Plans", ""]
    for r in results:
        if "skipped" in r or not r["plan"]:
            continue
        lines += [f"### {r['name']}", f"> {r['message']}", "",
                  "```json", json.dumps(r["plan"], indent=2), "```", ""]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--category", default="computer_vision")
    ap.add_argument("--label", default="run",
                    help="what this run is testing (e.g. 'baseline', 'plan_answer') — "
                         "names the report so --compare can find it")
    ap.add_argument("--only", help="comma-separated case names")
    ap.add_argument("--repeat", type=int, default=1, metavar="N",
                    help="run each case N times and report a pass rate; a single sample "
                         "per case is too noisy to attribute a prompt change to")
    ap.add_argument("--compare", nargs=2, metavar=("BEFORE", "AFTER"),
                    help="diff the newest run of each label and show only what changed")
    args = ap.parse_args()

    if args.compare:
        raise SystemExit(compare(*args.compare))

    cases = CASES
    if args.only:
        wanted = {n.strip() for n in args.only.split(",")}
        cases = [c for c in CASES if c["name"] in wanted]

    try:
        gap_id, idea_id = _a_gap(args.category), _an_idea(args.category)
    except Exception as exc:  # noqa: BLE001 — an unreadable workspace shouldn't traceback
        print(f"could not read workspace '{args.category}': {type(exc).__name__}: {exc}")
        raise SystemExit(1)
    print(f"category {args.category} · gap {gap_id} · "
          f"idea {idea_id or '(none — refinement cases will skip)'}")
    print(f"{len(cases)} case(s), one LLM call each — expect ~5-15s per case\n", flush=True)

    # The report directory is created up front and rewritten after every case, so a
    # Ctrl+C or a crash still leaves everything that ran — a 12-case pass is minutes of
    # API calls and losing it to an interrupt is worse than the rewrite cost.
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = EVAL_DIR / f"{ts}_plan_{args.label}"
    run_dir.mkdir(parents=True, exist_ok=True)
    print(f"report: {run_dir}/report.md  (updated after each case)\n", flush=True)

    errors_path = run_dir / "errors.log"

    def log_error(case_name: str, attempt: int, exc: BaseException) -> None:
        """Full traceback to errors.log — the console only gets the one-line reason."""
        with errors_path.open("a", encoding="utf-8") as fh:
            fh.write(f"\n{'=' * 70}\n{datetime.now().isoformat(timespec='seconds')}  "
                     f"{case_name}  attempt {attempt}/{_CASE_ATTEMPTS}\n{'=' * 70}\n")
            fh.write("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))

    def save(results: list[dict]) -> None:
        """Never let a reporting bug lose the results that cost API calls to get."""
        try:
            (run_dir / "raw.json").write_text(json.dumps(results, indent=2, default=str))
            (run_dir / "report.md").write_text(
                render_markdown(args.label, args.category, results))
        except Exception as exc:  # noqa: BLE001
            print(f"  \033[31m(could not write report: {exc})\033[0m", flush=True)

    results: list[dict] = []
    try:
        for i, case in enumerate(cases, 1):
            runs = []
            for rep in range(args.repeat):
                suffix = f" ({rep + 1}/{args.repeat})" if args.repeat > 1 else ""
                print(f"  [{i}/{len(cases)}] {case['name']:<26} ...{suffix}",
                      end="\r", flush=True)
                run = run_case(case, args.category, gap_id, idea_id, log_error)
                if "skipped" in run:
                    runs = [run]
                    break
                runs.append(run)

            row = runs[0] if "skipped" in runs[0] else aggregate(
                case["name"], case["message"], runs[0]["focus"], runs)
            results.append(row)
            save(results)

            if "skipped" in row:
                mark = f"\033[33mskip\033[0m  {row['skipped']}"
            elif row.get("error"):
                mark = f"\033[35mERROR\033[0m {row['error']}  (see errors.log)"
            elif not row["fails"]:
                mark = f"\033[32m{_verdict(row)}\033[0m"
            else:
                mark = f"\033[31m{_verdict(row)}\033[0m  " + "; ".join(row["fails"])
            retried = "" if row.get("attempts", 1) == 1 else f" [try {row['attempts']}]"
            print(f"  [{i}/{len(cases)}] {case['name']:<26} {mark}{retried}", flush=True)
    except KeyboardInterrupt:
        print("\ninterrupted — keeping the cases that finished", flush=True)
        save(results)

    ran = [r for r in results if "skipped" not in r]
    total = sum(r.get("n", 1) for r in ran)
    passes = sum(r.get("passes", 0) for r in ran)
    errored = [r for r in ran if r.get("error")]
    flaky = [r for r in ran if 0 < r.get("passes", 0) < r.get("n", 1)]
    print(f"\n{passes}/{total} passed"
          + (f" · {len(errored)} errored (errors.log)" if errored else "")
          + (f" · {len(flaky)} flaky: {', '.join(r['name'] for r in flaky)}" if flaky else ""))
    print(f"report: {run_dir}/report.md")
    if args.repeat == 1:
        print("\033[2m(single sample per case — use --repeat 3 before trusting a delta)\033[0m")


if __name__ == "__main__":
    main()
