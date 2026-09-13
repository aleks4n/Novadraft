"""Paper agent package — registry manifest and public entry points.

The pipeline is fetch → extract → summarize → gap, run as one streamed graph. The
``MANIFEST`` below is what ``src/common/registry.py`` auto-discovers.
"""
from src.common.tools.paper_fetch import fetch as paper_fetch
from src.common.tools.paper_extract import extract as paper_extract
from src.agents.paper_agent.subagents.gap import extract_gaps
from src.agents.paper_agent.subagents.summarize import summarize

from .agent import build_paper_graph, run

__all__ = ["run", "build_paper_graph", "MANIFEST"]


# Reused by both the top-level form and the paper_extract / summarize / gap
# subagents, which all key off a workspace slug + a paper id.
_PAPER_ID_FIELDS = [
    {"name": "category_slug", "type": "text", "required": True,
     "placeholder": "transformers"},
    {"name": "paper_id", "type": "text", "required": True,
     "placeholder": "arxiv_1706.03762"},
    {"name": "force", "type": "text", "required": False, "placeholder": "false"},
]


MANIFEST = {
    "kind": "agent",
    "run": run,
    "graph_builder": build_paper_graph,
    "workspace_field": "category_slug",
    "steps": ["fetch", "extract", "summarize", "gap"],
    "examples": [
        {
            "name": "paper_fetch_arxiv.json",
            "label": "arXiv paper",
            "description": "Standard case: query by arXiv ID.",
        },
        {
            "name": "paper_fetch_gan_no_arxiv.json",
            "label": "GAN paper, no arXiv ID",
            "description": (
                "Goodfellow et al.'s GAN paper as reprinted in Communications "
                "of the ACM (DOI-only, crossref+unpaywall resolution, no "
                "arXiv ID exists for this record)."
            ),
        },
        {
            "name": "paper_fetch_gan_closed_source.json",
            "label": "GAN paper, closed source / paywalled",
            "description": (
                "A GAN paper resolved to a paywalled Springer book chapter "
                "with no open-access PDF -- exercises the "
                "no_open_access_pdf / source_unavailable warning path."
            ),
        },
    ],
    "schema": [
        {"name": "category_slug", "type": "text", "required": True,
         "placeholder": "transformers"},
        {"name": "query", "type": "text", "required": True,
         "placeholder": "arXiv ID, URL, DOI, or paper title"},
        {"name": "force", "type": "text", "required": False,
         "placeholder": "false"},
    ],
    "subagents": [
        {
            "name": "paper_fetch",
            "run": lambda category_slug, query, **_: paper_fetch(category_slug, query),
            "schema": [
                {"name": "category_slug", "type": "text", "required": True,
                 "placeholder": "transformers"},
                {"name": "query", "type": "text", "required": True,
                 "placeholder": "arXiv ID, URL, DOI, or paper title"},
            ],
        },
        {
            "name": "paper_extract",
            "run": lambda category_slug, paper_id, force=False, **_:
                paper_extract(category_slug, paper_id, force=force),
            "schema": _PAPER_ID_FIELDS,
        },
        {
            "name": "summarize",
            "run": lambda category_slug, paper_id, force=False, **_:
                summarize(category_slug, paper_id, force=force),
            "schema": _PAPER_ID_FIELDS,
        },
        {
            "name": "gap",
            "run": lambda category_slug, paper_id, force=False, **_:
                extract_gaps(category_slug, paper_id, force=force),
            "schema": _PAPER_ID_FIELDS,
        },
    ],
}
