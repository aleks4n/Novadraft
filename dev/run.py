"""Simple dev/test runner for agents, subagents, and flows.

Usage:
    python -m dev.run list
    python -m dev.run agent paper_agent --kwargs '{"category_slug": "transformers", "query": "1706.03762"}'
    python -m dev.run subagent paper_fetch --fixture sample_paper.json
    python -m dev.run flow paper_then_survey --fixture demo_flow.json

Every invocation writes a JSON log to dev/runs/{timestamp}_{name}.json
containing: name, kind, inputs, output, error, duration, model, timestamp.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _env_bootstrap  # noqa: F401,E402

import argparse  # noqa: E402
import json  # noqa: E402
import time  # noqa: E402
import traceback  # noqa: E402
from datetime import datetime  # noqa: E402

from dev.registry import AGENTS, SUBAGENTS, FLOWS, list_all  # noqa: E402

HERE = Path(__file__).resolve().parent
RUNS_DIR = HERE / "runs"
FIXTURES_DIR = HERE / "fixtures"

TABLES = {"agent": AGENTS, "subagent": SUBAGENTS, "flow": FLOWS}


def _load_inputs(args) -> dict:
    if args.fixture:
        with open(FIXTURES_DIR / args.fixture) as f:
            return json.load(f)
    if args.kwargs:
        return json.loads(args.kwargs)
    return {}


def _log(record: dict, name: str) -> Path:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = RUNS_DIR / f"{ts}_{name}.json"
    with open(path, "w") as f:
        json.dump(record, f, indent=2, default=str)
    return path


def _run(kind: str, name: str, inputs: dict, on_step=None) -> dict:
    table = TABLES.get(kind)
    if table is None:
        raise SystemExit(f"Unknown kind: {kind}. Available: {sorted(TABLES)}")
    if name not in table:
        raise SystemExit(f"Unknown {kind}: {name}. Available: {sorted(table)}")
    fn = table[name]
    record = {
        "name": name,
        "kind": kind,
        "inputs": inputs,
        "timestamp": datetime.now().isoformat(),
        "output": None,
        "error": None,
        "duration_s": None,
    }
    kwargs = dict(inputs)
    if on_step is not None:
        kwargs["on_step"] = on_step
    t0 = time.time()
    try:
        record["output"] = fn(**kwargs)
    except Exception as e:
        record["error"] = f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
    record["duration_s"] = round(time.time() - t0, 3)
    return record


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="list available agents, subagents, and flows")

    for kind in ("agent", "subagent", "flow"):
        sp = sub.add_parser(kind)
        sp.add_argument("name")
        sp.add_argument("--kwargs", help="inline JSON dict of inputs")
        sp.add_argument("--fixture", help="filename in dev/fixtures/")

    args = p.parse_args()

    if args.cmd == "list":
        print(json.dumps(list_all(), indent=2))
        return

    inputs = _load_inputs(args)
    record = _run(args.cmd, args.name, inputs)
    log_path = _log(record, args.name)

    print(f"\n--- {args.cmd}: {args.name} ({record['duration_s']}s) ---")
    if record["error"]:
        print("ERROR:", record["error"], file=sys.stderr)
    else:
        out = record["output"]
        print(out if isinstance(out, str) else json.dumps(out, indent=2, default=str))
    print(f"\nlogged: {log_path.relative_to(HERE.parent)}")


if __name__ == "__main__":
    main()
