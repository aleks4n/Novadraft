"""The category_agent graph's state — what flows between its top-level nodes.

A LangGraph ``TypedDict`` (``total=False`` so each node returns only the keys it
sets), mirroring the other agents' ``state.py``. The category agent *bootstraps*
a workspace: given a free-text ``topic`` it researches the field, has an LLM
write a short category description (plus a few seminal papers), and writes the
``meta.json`` that ``storage.ensure_category`` requires before any paper can be
ingested.

Linear flow: research the topic -> describe it (LLM) -> write the category.
``result`` is what ``run()`` returns.
"""
from __future__ import annotations

from typing import TypedDict


class CategoryState(TypedDict, total=False):
    # inputs
    topic: str                 # free-text field/topic the user wants to create
    category_slug: str         # filename-safe slug derived from ``topic``

    # working set
    background: str            # grounding text gathered in the research node
    background_file: str       # where the full research dossier was saved

    # LLM-written profile
    name: str                  # human-readable category name
    description: str           # short description of the category (2–4 sentences)
    seminal_papers: list[dict] # a few foundational papers of the field

    # output
    result: dict
