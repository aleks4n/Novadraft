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
    python -m dev.eval_orchestrator_plans --label baseline
    python -m dev.eval_orchestrator_plans --label fewshot --category computer_vision
    python -m dev.eval_orchestrator_plans --label quick --only chat_greeting,gap_generate
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import _env_bootstrap  # noqa: F401,E402

from src.common.schemas.gap import GapRecord  # noqa: E402
from src.common.utils import storage  # noqa: E402
from src.agents.orchestrator_agent.context import assemble_context  # noqa: E402
from src.agents.orchestrator_agent.schemas import FocusSelection, Plan  # noqa: E402
from src.agents.orchestrator_agent.subagents.plan.agent import plan as plan_node  # noqa: E402
from src.agents.orchestrator_agent.tools.registry import TOOLS  # noqa: E402

EVAL_DIR = Path(__file__).resolve().parent / "eval_runs"


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
     "expect": {"tool": "creative_thinker", "thinking_mode": "generation"}},
    {"name": "gap_generate_constrained", "focus": "gap",
     "message": "Give me 3 ideas for this gap, nothing that needs retraining",
     "expect": {"tool": "creative_thinker", "thinking_mode": "generation"}},
    {"name": "gap_generate_implicit", "focus": "gap", "message": "what could we do here?",
     "expect": {"tool": "creative_thinker", "thinking_mode": "generation"}},

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

    # The mode has to match what was actually focused: refinement works on ONE
    # existing idea, so planning it with no idea in context cannot succeed.
    for i, step in enumerate(plan.steps, 1):
        if step.tool != "creative_thinker":
            continue
        mode = step.args.get("thinking_mode")
        if mode == "refinement" and not focus.idea_ids:
            fails.append(f"step {i}: refinement planned with no idea in focus")
        if mode == "generation" and not (focus.gap_ids or focus.idea_ids):
            fails.append(f"step {i}: generation planned with nothing in focus")

    # Per-case expectations.
    if "steps" in expect and len(plan.steps) != expect["steps"]:
        fails.append(f"expected {expect['steps']} step(s), got {len(plan.steps)}")
    if "tool" in expect:
        used = [s.tool for s in plan.steps]
        if expect["tool"] not in used:
            fails.append(f"expected tool {expect['tool']!r}, got {used or 'none'}")
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


def run_case(case: dict, category: str, gap_id: str | None, idea_id: str | None) -> dict:
    kind = case["focus"]
    if kind == "gap":
        focus = FocusSelection(gap_ids=[gap_id]) if gap_id else None
    elif kind == "idea":
        focus = FocusSelection(idea_ids=[idea_id]) if idea_id else None
    else:
        focus = FocusSelection()

    if focus is None:
        return {"name": case["name"], "skipped": f"no {kind} available in '{category}'"}

    state = {"category_slug": category, "session_id": "session_eval",
             "run_id": "run_eval", "message": case["message"], "focus": focus,
             "iteration": 0}
    state.update(assemble_context(state))

    t0 = time.time()
    try:
        plan: Plan = plan_node(state)["plan"]
    except Exception as exc:  # noqa: BLE001 — a crash is a failure, not an abort
        return {"name": case["name"], "message": case["message"],
                "seconds": round(time.time() - t0, 1),
                "fails": [f"plan raised {type(exc).__name__}: {exc}"], "plan": None}

    fails = check(plan, case["expect"], focus)
    return {
        "name": case["name"],
        "message": case["message"],
        "focus": kind,
        "seconds": round(time.time() - t0, 1),
        "fails": fails,
        "plan": plan.model_dump(mode="json"),
    }


def render_markdown(label: str, category: str, results: list[dict]) -> str:
    ran = [r for r in results if "skipped" not in r]
    passed = [r for r in ran if not r["fails"]]
    lines = [
        f"# Orchestrator plan eval — {label}",
        "",
        f"- category: `{category}`",
        f"- {len(passed)}/{len(ran)} passed"
        + (f" · {len(results) - len(ran)} skipped" if len(ran) != len(results) else ""),
        "",
        "| case | focus | steps | tool | mode | s | result |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in results:
        if "skipped" in r:
            lines.append(f"| {r['name']} | — | — | — | — | — | skipped: {r['skipped']} |")
            continue
        p = r["plan"] or {"steps": []}
        steps = p["steps"]
        tools = ", ".join(s["tool"] for s in steps) or "—"
        modes = ", ".join(str(s["args"].get("thinking_mode", "")) for s in steps
                          if s["tool"] == "creative_thinker") or "—"
        verdict = "pass" if not r["fails"] else "FAIL: " + "; ".join(r["fails"])
        lines.append(f"| {r['name']} | {r['focus']} | {len(steps)} | {tools} | {modes} "
                     f"| {r['seconds']} | {verdict} |")

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
    ap.add_argument("--label", default="run")
    ap.add_argument("--only", help="comma-separated case names")
    args = ap.parse_args()

    cases = CASES
    if args.only:
        wanted = {n.strip() for n in args.only.split(",")}
        cases = [c for c in CASES if c["name"] in wanted]

    gap_id, idea_id = _a_gap(args.category), _an_idea(args.category)
    print(f"category {args.category} · gap {gap_id} · idea {idea_id or '(none — '
          f'refinement cases will skip)'}\n")

    results = []
    for i, case in enumerate(cases, 1):
        result = run_case(case, args.category, gap_id, idea_id)
        results.append(result)
        if "skipped" in result:
            mark = f"\033[33mskip\033[0m  {result['skipped']}"
        elif result["fails"]:
            mark = "\033[31mFAIL\033[0m  " + "; ".join(result["fails"])
        else:
            mark = "\033[32mpass\033[0m"
        print(f"  [{i}/{len(cases)}] {case['name']:<26} {mark}")

    ran = [r for r in results if "skipped" not in r]
    passed = [r for r in ran if not r["fails"]]
    print(f"\n{len(passed)}/{len(ran)} passed")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = EVAL_DIR / f"{ts}_plan_{args.label}"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "raw.json").write_text(json.dumps(results, indent=2, default=str))
    (run_dir / "report.md").write_text(render_markdown(args.label, args.category, results))
    print(f"report: {run_dir}/report.md")


if __name__ == "__main__":
    main()
