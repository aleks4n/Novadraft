"""The director_agent graph's state - what flows between its top-level nodes.

A LangGraph TypedDict; `total=False` so each node returns only the keys it sets.
The linear flow: load master -> collect -> compare/merge -> persist -> return.

`result` is the final value `run()` returns.
"""
from __future__ import annotations

from typing import TypedDict


class DirectorState(TypedDict, total=False):
	# inputs
	paper_json_paths: list[str]
	force: bool

	# per-stage outputs
	master_gap_list_path: str
	master_gaps: list[dict]
	paper_gap_strings: list[str]
	incoming_gaps: list[dict]
	merged_gaps: list[dict]

	# control / output
	result: str
