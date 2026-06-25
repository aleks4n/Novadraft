"""Agent + subagent + flow registry for the dev/test harness.

Three kinds of entries:
  - AGENTS:    top-level flows the user runs (e.g. ``paper_agent``, which wires
                fetch -> extract -> summarize -> gap into one call). These
                return all sub-results (one JSON blob per step) so the dev UI
                can show what each step produced.
  - SUBAGENTS: the individual stages kept for isolated testing
                (``paper_fetch``, ``paper_extract``, ``summarize``, ``gap``).
                ``summarize`` and ``gap`` each run their own bounded
                extractor<->critic loop internally — there's no separate
                "critique-only" or "loop-only" entry, since the loop isn't
                exposed outside either subagent.
  - FLOWS:     multi-agent flows spanning more than one top-level agent.

Add new entries here. Each entry is a callable that takes a dict of kwargs
(parsed from CLI / fixture) and returns a JSON-serializable result.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Make the package importable when running this file directly.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.common.tools.paper_fetch import fetch as paper_fetch  # noqa: E402
from src.common.tools.paper_extract import extract as paper_extract  # noqa: E402
from src.agents.paper_agent import run as paper_agent  # noqa: E402
from src.agents.paper_agent.subagents.gap import extract_gaps  # noqa: E402
from src.agents.paper_agent.subagents.summarize import summarize  # noqa: E402


def _truthy(v) -> bool:
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in {"1", "true", "yes", "y", "on"}


AGENTS = {
    "paper_agent": lambda category_slug, query, force=False, **_:
        paper_agent(category_slug, query, force=_truthy(force)),
}

# Agents that call ``runlog.start_run`` internally (see
# src/common/utils/runlog.py) and so write a live-tailable JSON log to
# workspace/<category_slug>/logs/<agent>/. The dev UI polls that file for
# progress instead of a bespoke callback. Keyed by the input field that
# holds the workspace/category slug.
LOGGED_AGENTS = {
    "paper_agent": "category_slug",
}

# Steps a logged agent is expected to go through, in pipeline order. Purely
# a display hint for the dev UI (e.g. to label what's still pending) — the
# authoritative step list is whatever actually shows up in the run's JSON
# log, since not every run takes the same path (e.g. an ambiguous fetch
# skips everything downstream).
AGENT_STEPS = {
    "paper_agent": ["fetch", "extract", "summarize", "gap"],
}

SUBAGENTS = {
    "paper_fetch": lambda category_slug, query, **_: paper_fetch(category_slug, query),
    "paper_extract": lambda category_slug, paper_id, force=False, **_:
        paper_extract(category_slug, paper_id, force=_truthy(force)),
    "summarize": lambda category_slug, paper_id, force=False, **_:
        summarize(category_slug, paper_id, force=_truthy(force)),
    "gap": lambda category_slug, paper_id, force=False, **_:
        extract_gaps(category_slug, paper_id, force=_truthy(force)),
}

FLOWS = {}


# Input schemas drive the UI form. Field types: "text", "textarea", "list[textarea]".
SCHEMAS = {
    "agent": {
        "paper_agent": [
            {"name": "category_slug", "type": "text", "required": True,
             "placeholder": "transformers"},
            {"name": "query", "type": "text", "required": True,
             "placeholder": "arXiv ID, URL, DOI, or paper title"},
            {"name": "force", "type": "text", "required": False,
             "placeholder": "false"},
        ],
    },
    "subagent": {
        "paper_fetch": [
            {"name": "category_slug", "type": "text", "required": True,
             "placeholder": "transformers"},
            {"name": "query", "type": "text", "required": True,
             "placeholder": "arXiv ID, URL, DOI, or paper title"},
        ],
        "paper_extract": [
            {"name": "category_slug", "type": "text", "required": True,
             "placeholder": "transformers"},
            {"name": "paper_id", "type": "text", "required": True,
             "placeholder": "arxiv_1706.03762"},
            {"name": "force", "type": "text", "required": False,
             "placeholder": "false"},
        ],
        "summarize": [
            {"name": "category_slug", "type": "text", "required": True,
             "placeholder": "transformers"},
            {"name": "paper_id", "type": "text", "required": True,
             "placeholder": "arxiv_1706.03762"},
            {"name": "force", "type": "text", "required": False,
             "placeholder": "false"},
        ],
        "gap": [
            {"name": "category_slug", "type": "text", "required": True,
             "placeholder": "transformers"},
            {"name": "paper_id", "type": "text", "required": True,
             "placeholder": "arxiv_1706.03762"},
            {"name": "force", "type": "text", "required": False,
             "placeholder": "false"},
        ],
    },
    "flow": {},
}


def list_all() -> dict:
    return {
        "agents": sorted(AGENTS),
        "subagents": sorted(SUBAGENTS),
        "flows": sorted(FLOWS),
        "schemas": SCHEMAS,
        "agent_steps": AGENT_STEPS,
        "logged_agents": LOGGED_AGENTS,
    }
