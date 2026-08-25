"""Orchestrator agent — the ideation phase's entry point.

Takes one user turn (a message plus whatever they selected in the UI), plans which
subagents to call, runs them, and offers back draft ideas. Accepting a draft is a
separate, LLM-free call (`commit_selection`) — the agent never writes to the idea graph
on its own.
"""
from .agent import build_orchestrator_graph, commit_selection, input_adapter, run, run_turn

__all__ = [
    "run",
    "run_turn",
    "commit_selection",
    "build_orchestrator_graph",
    "input_adapter",
    "MANIFEST",
]


MANIFEST = {
    "kind": "agent",
    "run": run,
    "graph_builder": build_orchestrator_graph,
    "workspace_field": "category_slug",
    "input_adapter": input_adapter,
    "steps": ["assemble_context", "plan", "execute", "reflect", "finish"],
    "schema": [
        {
            "name": "category_slug",
            "type": "text",
            "required": True,
            "placeholder": "transformers",
        },
        {
            "name": "message",
            "type": "text",
            "required": True,
            "placeholder": "Give me 3 ideas for this gap",
        },
        {
            "name": "gap_ids",
            "type": "text",
            "required": False,
            "placeholder": "gap_0963b7ff (comma or space separated)",
        },
        {
            "name": "idea_ids",
            "type": "text",
            "required": False,
            "placeholder": "idea_464a2325 — for refinement",
        },
        {
            "name": "session_id",
            "type": "text",
            "required": False,
            "placeholder": "blank = new session",
        },
    ],
}
