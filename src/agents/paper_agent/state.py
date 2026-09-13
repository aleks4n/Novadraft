"""The paper_agent graph state — what flows between its top-level nodes.

A LangGraph ``TypedDict``; ``total=False`` so each node returns only the keys it
sets. Linear flow: query → fetch → extract → summarize → gap → assemble.

``summary_result``/``gaps_result`` are the dicts the summarize/gap subgraphs
return. ``result`` is the final value ``run()`` returns.
"""
from __future__ import annotations

from typing import TypedDict


class PaperState(TypedDict, total=False):
    # inputs
    category_slug: str
    query: str
    force: bool

    # per-stage outputs
    fetch_result: dict
    paper_id: str
    extract_result: dict
    summary_result: dict
    gaps_result: dict

    # control / output
    ambiguous: bool     # fetch came back ambiguous → nothing downstream can run
    needs_source: bool  # paper resolved but full text is unavailable (restricted /
                        # paywalled) → wait for the user to upload a pdf/tex source
    result: dict        # final return value of run()
