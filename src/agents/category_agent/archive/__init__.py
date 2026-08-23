"""Archived category-agent variant — the LLM/LangGraph version.

Kept for later: given a topic it researches the field (Wikipedia), then has an
LLM write a name + short description + a few seminal papers into meta.json.
Superseded as the *live* agent by the top-level ``agent.py``, which takes only a
short Wikipedia description (no LLM). Self-contained: its own ``state.py`` lives
beside it here so it never breaks when the live agent's state changes.

To revive it, point ``category_agent/__init__.py``'s MANIFEST at
``build_category_graph`` / ``run`` from this module.
"""
from .agent import build_category_graph, run  # noqa: F401
