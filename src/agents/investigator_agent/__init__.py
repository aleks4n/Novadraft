"""Investigator agent — free-text research coordinator.

The agent routes a prompt to either the paper pipeline or a lightweight web
search/scrape path and returns plain text.
"""
from .agent import build_investigator_graph, run

__all__ = ["run", "build_investigator_graph", "MANIFEST"]


MANIFEST = {
	"kind": "agent",
	"run": run,
	"graph_builder": build_investigator_graph,
	"steps": ["interpret", "research", "render"],
	"schema": [
		{
			"name": "text",
			"type": "text",
			"required": True,
			"placeholder": "search and download the paper: attention is all you need",
		},
		{
			"name": "session_id",
			"type": "text",
			"required": True,
			"placeholder": "research-session-123",
		},
		{
			"name": "category_slug",
			"type": "text",
			"required": True,
			"placeholder": "machine-learning",
			"help": "Validator category; sessions are stored under sessions/<session_id>.",
		},
	],
}
