from .agent import build_director_graph, run

__all__ = ["run", "build_director_graph", "MANIFEST"]

MANIFEST = {
    "kind": "agent",
    "run": run,
    "graph_builder": build_director_graph,
    "workspace_field": "category_slug",
    "steps": [
        "load_known",
        "collect_pending",
        "dedup",
        "finalize",
    ],
    "schema": [
        {
            "name": "category_slug",
            "type": "text",
            "required": True,
            "placeholder": "transformers",
        },
    ],
}
