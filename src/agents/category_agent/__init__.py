"""Category agent — LLM-write a short technical category description.

The ``MANIFEST`` below is what the dev harness auto-discovers (see
``dev/registry.py``). The agent (``agent.py``) asks the LLM (fast ``cheap``
role) for a short technical description of the topic and writes ``meta.json``.
The description is just a starting point — the app UI lets the user edit it by
hand afterwards.
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
