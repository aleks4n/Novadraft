"""Category agent — fetch a short Wikipedia description and write the category.

The ``MANIFEST`` below is what the dev harness auto-discovers (see
``dev/registry.py``). The live agent (``agent.py``) takes a short Wikipedia
description and writes ``meta.json``; the original full-dossier fetcher is kept in
``old_agent.py``, and the richer LLM variant (name + description + seminal papers)
is archived under ``archive/`` for later use.
"""
from .agent import build_category_graph, run

__all__ = ["run", "build_category_graph", "MANIFEST"]

MANIFEST = {
    "kind": "agent",
    "run": run,
    "graph_builder": build_category_graph,
    # The agent *creates* the workspace, so there is no pre-existing slug field on
    # the form. The dev UI logs to its shared graph_logs dir; the standalone
    # ``run()`` logs under the workspace it creates.
    "workspace_field": None,
    "steps": [
        "fetch",
        "write_category",
    ],
    "schema": [
        {
            "name": "topic",
            "type": "text",
            "required": True,
            "placeholder": (
                "e.g. Machine Learning, Natural Language Processing, Computer Vision"
            ),
        },
    ],
}
