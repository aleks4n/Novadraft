"""Full-pipeline graph composition (paper → director → validator).

Kept separate from ``src/agents/*`` (which hold the individual agents): this
package wires them into one end-to-end ingestion graph, plus a ``resume`` graph
that runs just the director+validator tail for gaps that haven't finished (using
each gap's stage folder as the checkpoint). Both ``MANIFEST``s are discovered by
``dev/registry.py`` just like an agent's, so the dev harness and app backend can
stream ``pipeline`` / ``resume`` with the shared logging engine.
"""
from .pipeline import build_pipeline_graph, build_resume_graph, resume, run

__all__ = [
    "run", "resume", "build_pipeline_graph", "build_resume_graph",
    "MANIFEST", "MANIFEST_RESUME",
]

MANIFEST = {
    "kind": "agent",
    "run": run,
    "graph_builder": build_pipeline_graph,
    "workspace_field": "category_slug",
    "steps": ["paper", "director", "validate_gap", "rank_gap", "collect"],
    "schema": [
        {"name": "category_slug", "type": "text", "required": True,
         "placeholder": "transformers"},
        {"name": "query", "type": "text", "required": True,
         "placeholder": "arXiv ID, URL, DOI, or paper title"},
        {"name": "force", "type": "text", "required": False, "placeholder": "false"},
    ],
}

MANIFEST_RESUME = {
    "kind": "agent",
    "run": resume,
    "graph_builder": build_resume_graph,
    "workspace_field": "category_slug",
    "steps": ["plan", "director", "validate_gap", "rank_gap", "collect"],
    "schema": [
        {"name": "category_slug", "type": "text", "required": True,
         "placeholder": "transformers"},
        {"name": "gap_id", "type": "text", "required": False,
         "placeholder": "gap_9f0f24c1 (resume one gap)"},
        {"name": "paper_id", "type": "text", "required": False,
         "placeholder": "arxiv_1706_03762 (resume a paper's gaps)"},
    ],
}
