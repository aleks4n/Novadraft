"""Category agent package — registry manifest and public entry points.

The ``MANIFEST`` below is what ``src/common/registry.py`` auto-discovers. The
agent itself lives in ``agent.py``.
"""
from .agent import build_category_graph, run

__all__ = ["run", "build_category_graph", "MANIFEST"]

MANIFEST = {
    "kind": "agent",
    "run": run,
    "graph_builder": build_category_graph,
    # The agent creates the workspace, so there is no pre-existing slug field on
    # the form; ``run()`` logs under the workspace it creates.
    "workspace_field": None,
    "steps": [
        "describe",
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
