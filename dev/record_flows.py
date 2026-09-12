"""Write the flows document from the scenario tests.

    python -m dev.record_flows          # writes docs/ORCHESTRATOR_FLOWS.md

The depiction is generated rather than written by hand, and from the same scenarios the
test suite asserts on. A flow that changes shape changes the document on the next run; one
that changes shape *without* anyone noticing fails a test first. Prose kept beside code
drifts — this cannot.

Each scenario is replayed through the real graph with the LLM steps and the subagents
recorded, and what comes out is the actual call chain with the actual arguments, not a
description of what it should be.
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import _env_bootstrap  # noqa: F401,E402

import pytest  # noqa: E402

OUT = ROOT / "docs" / "ORCHESTRATOR_FLOWS.md"

# Who each recorded participant is, in the reader's terms rather than the code's.
_WHO = {
    "plan.llm": "**plan** (LLM)",
    "reflect.llm": "**reflect** (LLM)",
    "creative_thinker": "**creative thinker**",
    "investigator": "**investigator**",
}

# Arguments worth showing per participant, in the order they answer "what was it asked".
_SHOW = {
    "creative_thinker": ("mode", "subject", "subject_title", "styles", "width", "depth",
                         "tool_use", "can_investigate", "budget_llm_calls",
                         "context_gaps", "context_ideas",
                         "context_user_input", "context_findings", "task"),
    "investigator": ("query", "session_id", "category_slug"),
}


class _Collector:
    """Gathers one row per scenario as the tests run."""

    def __init__(self) -> None:
        self.rows: list[dict] = []

    def pytest_runtest_makereport(self, item, call):
        if call.when != "call" or call.excinfo is not None:
            return
        recorder = item.funcargs.get("rec")
        if recorder is None:
            return
        self.rows.append({
            "name": item.name,
            "doc": inspect.getdoc(item.function) or "",
            "calls": recorder.calls,
        })


def _title(name: str) -> str:
    return name.removeprefix("test_").replace("_", " ").capitalize()


def _arguments(call) -> list[str]:
    keys = _SHOW.get(call.who)
    if keys is None:
        return []
    lines = []
    for key in keys:
        value = call.args.get(key)
        if value in (None, "", [], 0, False):
            continue
        lines.append(f"      {key}: {value!r}")
    return lines


def _render(row: dict) -> str:
    doc = row["doc"].split("\n\n")
    headline = doc[0].replace("\n", " ")
    rest = "\n\n".join(p.replace("\n", " ") for p in doc[1:])

    out = [f"## {_title(row['name'])}", "", f"*{headline}*", ""]
    if rest:
        out += [rest, ""]

    out += ["```"]
    if not row["calls"]:
        out.append("(nothing outside the orchestrator was called)")
    for index, call in enumerate(row["calls"], 1):
        out.append(f"{index}. orchestrator → {_WHO.get(call.who, call.who)}")
        out.extend(_arguments(call))
    out += ["```", ""]
    return "\n".join(out)


def main() -> int:
    collector = _Collector()
    code = pytest.main(["-q", "--no-header", "-p", "no:cacheprovider",
                        str(ROOT / "tests" / "test_orchestrator_flows.py")],
                       plugins=[collector])
    if code != 0:
        print("scenarios failed — not writing a document that describes broken behaviour")
        return code

    header = [
        "# What happens on one turn",
        "",
        "Every scenario the orchestrator can take, recorded from the code rather than",
        "described alongside it. Each block is the real call chain from a real run through",
        "the graph, with the two LLM steps and the two subagents recorded; the arguments",
        "shown are the ones they were actually handed.",
        "",
        "Regenerate with `python -m dev.record_flows`. The same scenarios are asserted in",
        "`tests/test_orchestrator_flows.py`, so a flow cannot change here without either",
        "changing there or failing.",
        "",
        "---",
        "",
    ]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(header) + "\n".join(_render(r) for r in collector.rows),
                   encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)} — {len(collector.rows)} scenarios")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
