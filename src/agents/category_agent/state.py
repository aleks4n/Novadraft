"""The category_agent graph's state — what flows between its top-level nodes.

A LangGraph ``TypedDict`` (``total=False`` so each node returns only the keys it
sets), mirroring the other agents' ``state.py``. The live category agent
*bootstraps* a workspace from a **short Wikipedia description**: given a free-text
``topic`` it looks the topic up on Wikipedia, takes the short lead blurb, and
writes the ``meta.json`` that ``storage.ensure_category`` requires before any
paper can be ingested. No LLM.

Linear flow: fetch the Wikipedia description -> write the category. ``result`` is
what ``run()`` returns. (The richer LLM variant's state lives under ``archive/``.)
"""
from __future__ import annotations

from typing import TypedDict


class CategoryState(TypedDict, total=False):
    # inputs
    topic: str             # free-text field/topic the user wants to create
    category_slug: str     # filename-safe slug derived from ``topic``

    # fetched from Wikipedia
    name: str              # human-readable category name (the Wikipedia title)
    description: str       # short description of the category (Wikipedia lead)
    source_url: str        # the Wikipedia page the description came from
    warning: str           # set when no Wikipedia page was found

    # output
    result: dict
