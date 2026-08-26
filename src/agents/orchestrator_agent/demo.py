"""Run one ideation turn and print what each step did.

For showing the orchestrator without a UI: it streams the graph and renders every
node's output as it arrives — the context that was loaded, the plan the LLM wrote,
which tools ran, the reflection's verdict, and the drafts on offer.

    # generate from a gap — bare --gap picks one that parses
    python src/agents/orchestrator_agent/demo.py computer_vision --gap
    python src/agents/orchestrator_agent/demo.py computer_vision --gap gap_04e60ada

    # plain conversation — nothing selected, so the planner should call no tools at all
    python src/agents/orchestrator_agent/demo.py computer_vision -m "hi, what can you do?"

    # accept the drafts, so there is an idea to work on
    python src/agents/orchestrator_agent/demo.py computer_vision --accept

    # refine an existing idea
    python src/agents/orchestrator_agent/demo.py computer_vision --idea idea_1234abcd \\
        -m "make the evaluation plan concrete"

    # two turns of one conversation: the second selects nothing, and works from what the
    # first offered
    python src/agents/orchestrator_agent/demo.py --session demo -m "give me 2 ideas for this gap"
    python src/agents/orchestrator_agent/demo.py --session demo -m "make the second one more concrete"

Nothing reaches the idea graph unless you pass --accept: a run ends by *offering*
drafts, and accepting them is a separate call (`commit_selection`).
"""

from __future__ import annotations

import argparse
import sys
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.common.schemas.gap import GapRecord
from src.common.utils import storage
from src.agents.orchestrator_agent.agent import _get_graph, commit_selection
from src.agents.orchestrator_agent.tools.commit import offer_handle
from src.agents.orchestrator_agent.schemas import FocusSelection

W = 78
_ANY_GAP = object()  # bare `--gap`: focus a gap, any gap, rather than naming one


def _rule(label: str) -> None:
    print(f"\n\033[1m── {label} {'─' * max(0, W - len(label) - 4)}\033[0m")


def _kv(key: str, value: str, indent: str = "   ") -> None:
    print(f"{indent}\033[2m{key:<9}\033[0m {value}")


def _wrap(text: str, indent: str = "   ") -> str:
    """Naive wrap so long prose doesn't run off the terminal."""
    words, lines, cur = str(text).split(), [], ""
    for word in words:
        if len(cur) + len(word) + 1 > W - len(indent):
            lines.append(cur)
            cur = word
        else:
            cur = f"{cur} {word}".strip()
    lines.append(cur)
    return f"\n{indent}".join(lines)


def _first_valid_gap(category: str) -> str | None:
    """A gap that parses against the current schema — some fixtures predate it."""
    for stage in ("master", "to_rank", "pending"):
        for path in sorted(storage.gap_stage_dir(category, stage).glob("*.json")):
            _s, raw = storage.find_gap(category, path.stem)
            try:
                GapRecord.model_validate(raw)
                return path.stem
            except Exception:  # noqa: BLE001
                continue
    return None


# --- per-node renderers -------------------------------------------------------


def _show_context(update: dict) -> None:
    ctx = update["context"]
    _kv("loaded", f"{len(ctx.gaps)} gap(s) · {len(ctx.ideas)} idea(s) · {len(ctx.papers)} paper(s)")
    history = update.get("history") or []
    if history:
        _kv("history", f"{len(history)} earlier turn(s) in this session")
        for turn in history:
            print(f"   \033[2m› {_wrap(turn['message'], '     ')}\033[0m")
    for gap in ctx.gaps:
        print(f"   \033[36m{gap.gap_id}\033[0m  {gap.title}")
    for idea in ctx.ideas:
        print(f"   \033[36m{idea.idea_id}\033[0m  {idea.title}")
    for paper in ctx.papers:
        print(f"   \033[2m{paper.get('paper_id', '')}  {paper.get('title', '')}\033[0m")
    for item in ctx.user_input:
        unread = "" if item.extracted else "  \033[31m(text cannot be read)\033[0m"
        print(f"   \033[2m{item.kind}: {item.title or item.id}\033[0m{unread}")
    if ctx.missing_ids:
        print(f"   \033[31mcould not load: {', '.join(ctx.missing_ids)}\033[0m")


def _show_plan(update: dict) -> None:
    plan = update["plan"]
    _kv("goal", _wrap(plan.goal))
    if not plan.steps:
        # No tools: the planner answered directly and the run skips execute and reflect.
        # Show the reply here, or the trace jumps to finish looking like nothing happened.
        print("   \033[2mno tools needed — answering directly, skipping execute/reflect\033[0m")
        _kv("answer", _wrap(plan.answer))
        return
    print("   \033[2msteps\033[0m")
    for i, step in enumerate(plan.steps, 1):
        args = {k: v for k, v in step.args.items() if k != "instruction"}
        needs = f"  \033[35mneeds {step.needs}\033[0m" if step.needs else ""
        print(f"     {i}. \033[33m{step.tool}\033[0m{needs}  {args}")
        if step.why:
            print(f"        \033[2m{_wrap(step.why, '        ')}\033[0m")
        if step.args.get("instruction"):
            print(f"        \033[2minstruction: {_wrap(step.args['instruction'], '        ')}\033[0m")
    print("   \033[2mdone_criteria\033[0m")
    for crit in plan.done_criteria:
        print(f"     · {_wrap(crit, '       ')}")


def _show_execute(update: dict) -> None:
    for result in update["results"]:
        mark = "\033[32mok\033[0m" if result.ok else f"\033[31mFAILED: {result.error}\033[0m"
        print(f"   \033[33m{result.tool}\033[0m  {mark}  → {len(result.ideas)} draft(s)")
        if result.text:
            print(f"     \033[2m{_wrap(result.text, '     ')}\033[0m")


def _show_reflect(update: dict) -> None:
    judgment = update["judgment"]
    colour = {"finish": "32", "replan": "31"}.get(judgment.route, "0")
    keep = "" if judgment.route == "finish" else (
        "  \033[2m(keeping this round)\033[0m" if judgment.keep_results
        else "  \033[31m(discarding this round)\033[0m")
    _kv("route", f"\033[{colour}m{judgment.route}\033[0m{keep}   "
                 f"(iteration {update['iteration']})")
    if judgment.feedback:
        _kv("because", _wrap(judgment.feedback))


def _show_finish(update: dict, run_id: str) -> None:
    """The drafts on offer, each under the handle an accept would name it by.

    Sections are whatever the thinker chose to write — it names its own — so they are
    rendered in the record's order rather than from a fixed list of fields.
    """
    _kv("answer", _wrap(update["answer"]))
    ideas = update["ideas"]
    print(f"\n   \033[2m{len(ideas)} draft(s) offered — nothing written to the idea graph\033[0m")
    for index, idea in enumerate(ideas):
        handle = offer_handle(run_id, index, idea.idea_id)
        style = (idea.origin.style or "—") if idea.origin else "—"
        print(f"\n   \033[36m{handle}\033[0m  \033[2m[{style}]\033[0m")
        print(f"     \033[1m{idea.title}\033[0m")
        if idea.one_liner:
            print(f"     \033[2m{_wrap(idea.one_liner, '     ')}\033[0m")
        if idea.description:
            print(f"     {_wrap(idea.description, '     ')}")
        for key, value in idea.ordered_sections():
            print(f"     \033[2m{key}\033[0m")
            for item in (value if isinstance(value, list) else [value]):
                print(f"       - {_wrap(str(item), '         ')}")
        if idea.addresses:
            print(f"     \033[2maddresses: {', '.join(idea.addresses)}\033[0m")
        for relation in idea.relations:
            print(f"     \033[2m{relation.type} {relation.target}\033[0m")


_RENDER = {
    "assemble_context": _show_context,
    "plan": _show_plan,
    "execute": _show_execute,
    "reflect": _show_reflect,
}  # `finish` is dispatched separately — it needs the run id to name the offers.


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("category", nargs="?", default="computer_vision")
    parser.add_argument("--gap", nargs="?", const=_ANY_GAP, default=None,
                        help="focus a gap; bare --gap picks the first one that parses")
    parser.add_argument("--idea", help="focus this idea — use with a refinement message")
    parser.add_argument("-m", "--message", help="what to ask; default depends on the focus")
    parser.add_argument("--session",
                        help="continue this session — earlier turns and the drafts they "
                             "offered come back as context (default: a fresh one)")
    parser.add_argument("--accept", action="store_true",
                        help="commit the offered drafts afterwards (the only thing that writes)")
    args = parser.parse_args()

    category = args.category

    # Nothing selected unless you say so. Naming no gap and no idea is a real state — it
    # is what a chat turn looks like, and what a follow-up looks like once the session
    # carries the subject — so it is the default rather than something to opt into.
    focus = FocusSelection()
    focus_label = "nothing"
    default_message = "Hi, what can you do?"

    if args.idea:
        focus = FocusSelection(idea_ids=[args.idea])
        focus_label = f"idea {args.idea}"
        default_message = "Push this idea further — make it sharper."
    elif args.gap is not None:
        gap_id = _first_valid_gap(category) if args.gap is _ANY_GAP else args.gap
        if gap_id is None:
            print(f"No gap in '{category}' parses against the current schema.")
            raise SystemExit(1)
        focus = FocusSelection(gap_ids=[gap_id])
        focus_label = f"gap {gap_id}"
        default_message = "Give me 3 ideas for this gap."
    elif args.session:
        focus_label = "nothing — continuing the session"
        default_message = "Make the second one more concrete."

    message = args.message or default_message
    run_id = f"run_{uuid.uuid4().hex[:8]}"
    session_id = args.session or f"session_{uuid.uuid4().hex[:8]}"
    inputs = {
        "session_id": session_id,
        "category_slug": category,
        "run_id": run_id,
        "message": message,
        "focus": focus,
        "iteration": 0,
    }

    print(f"\033[1m{'═' * W}\033[0m")
    print(f"\033[1m ORCHESTRATOR\033[0m  {category}  ·  {session_id}  ·  {run_id}")
    _kv("message", message, indent=" ")
    _kv("focus", focus_label, indent=" ")
    print(f"\033[1m{'═' * W}\033[0m")

    offered: list = []
    for chunk in _get_graph().stream(inputs, stream_mode="updates"):
        for node, update in chunk.items():
            _rule(node)
            if node == "finish":
                _show_finish(update, run_id)
                offered = update.get("ideas", [])
            elif render := _RENDER.get(node):
                render(update)
            else:
                print(f"   {update}")

    print(f"\n\033[2mSession file: workspace/{category}/sessions/{session_id}/{run_id}.json\033[0m")
    print(f"\033[2mContinue this conversation:  --session {session_id} -m \"...\"\033[0m")

    if args.accept and offered:
        _rule("accept (what the user's click would do)")
        # By handle, not by `idea_id`: a generation draft has no id yet, so accepting by
        # id would match nothing and write nothing without saying why.
        picks = [offer_handle(run_id, index, idea.idea_id)
                 for index, idea in enumerate(offered)]
        written = commit_selection(category, session_id, run_id, picks)
        for idea_id in written:
            print(f"   \033[32mwrote\033[0m workspace/{category}/ideas/{idea_id}.json")
        print(f"\n   \033[2mrefine one with:  --idea {written[0]} -m \"...\"\033[0m" if written else "")
    elif offered:
        print("\033[2mNothing was written. Re-run with --accept to commit the drafts.\033[0m")


if __name__ == "__main__":
    main()
