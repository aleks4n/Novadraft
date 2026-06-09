"""Agent + flow registry for the dev/test harness.

Add new agents or flows here. Each entry is a callable that takes a dict of
kwargs (parsed from CLI / fixture) and returns a JSON-serializable result.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Make the package importable when running this file directly.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "paper_gap_agent"))

from paper_fetch import fetch as paper_fetch  # noqa: E402
from paper_extract import extract as paper_extract  # noqa: E402
from paper_summary import summarize as paper_summary  # noqa: E402
from gap_extract import extract_gaps as gap_extract  # noqa: E402


def _truthy(v) -> bool:
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in {"1", "true", "yes", "y", "on"}


AGENTS = {
    "paper_fetch": lambda category_slug, query, **_: paper_fetch(category_slug, query),
    "paper_extract": lambda category_slug, paper_id, force=False, **_:
        paper_extract(category_slug, paper_id, force=_truthy(force)),
    "paper_summary": lambda category_slug, paper_id, force=False, **_:
        paper_summary(category_slug, paper_id, force=_truthy(force)),
    "gap_extract": lambda category_slug, paper_id, force=False, **_:
        gap_extract(category_slug, paper_id, force=_truthy(force)),
}

FLOWS = {}


# Input schemas drive the UI form. Field types: "text", "textarea", "list[textarea]".
SCHEMAS = {
    "agent": {
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
        "paper_summary": [
            {"name": "category_slug", "type": "text", "required": True,
             "placeholder": "transformers"},
            {"name": "paper_id", "type": "text", "required": True,
             "placeholder": "arxiv_1706.03762"},
            {"name": "force", "type": "text", "required": False,
             "placeholder": "false"},
        ],
        "gap_extract": [
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
    return {"agents": sorted(AGENTS), "flows": sorted(FLOWS), "schemas": SCHEMAS}
