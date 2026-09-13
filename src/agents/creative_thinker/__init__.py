"""Creative thinker package — registry manifest and public entry points.

The divergent worker of the idea agent: it produces and develops candidate ideas.
:func:`think` returns an :class:`Envelope` and writes nothing.

The manifest declares no ``workspace_field``: this agent has no workspace of its
own, so run logs fall back to the shared graph-log directory.
"""

from .agent import build_creative_thinker_graph, input_adapter, post_run, run, think

__all__ = ["think", "run", "build_creative_thinker_graph", "MANIFEST"]


MANIFEST = {
    "kind": "agent",
    "run": run,
    "graph_builder": build_creative_thinker_graph,
    "input_adapter": input_adapter,
    "post_run": post_run,
    "steps": ["prepare", "generate | refine", "assemble"],
    "schema": [
        {
            "name": "idea",
            "type": "text",
            "required": False,
            "placeholder": "idea_sample.json  (fixture name, path, or inline JSON)",
        },
        {
            "name": "mode",
            "type": "text",
            "required": True,
            "placeholder": "generation | refinement",
        },
        {
            "name": "width",
            "type": "text",
            "required": True,
            "placeholder": "branches per expansion (generation: the fan-out)",
        },
        {
            "name": "depth",
            "type": "text",
            "required": False,
            "placeholder": "refinement only: tree depth · 1 = chain of thought",
        },
        {
            "name": "task",
            "type": "text",
            "required": False,
            "placeholder": "what this run should do — a question, a change, a critique",
        },
        {
            "name": "context",
            "type": "text",
            "required": False,
            "placeholder": "creative_thinker_context.json",
        },
        {
            "name": "styles",
            "type": "text",
            "required": False,
            "placeholder": "skeptic,transferer   (empty = roster order)",
        },
        {
            "name": "tool_use",
            "type": "text",
            "required": False,
            "placeholder": "false",
        },
        {
            "name": "max_llm_calls",
            "type": "text",
            "required": False,
            "placeholder": "60",
        },
        {
            "name": "investigator",
            "type": "text",
            "required": False,
            "placeholder": "none | web   (only used when tool_use is true)",
        },
        {
            "name": "persist",
            "type": "text",
            "required": False,
            "placeholder": "workspace slug — writes the run into that idea.json (off by default)",
        },
    ],
    # Showcase runs, in the order they tell the story: generate from gaps, then
    # develop what generation produced. Each points at a fixture the CLI can run
    # with ``--fixture`` too, so the dropdown and the CLI stay the same runs.
    "examples": [
        {
            "name": "creative_thinker_generation.json",
            "label": "1 · Generation — 4 stances on two gaps",
            "description": (
                "The breadth fan-out: four differently-styled thinkers on the same "
                "gaps, deduplicated against each other and against the ideas already "
                "in the workspace. No judge filters them and nothing is ranked."
            ),
        },
        {
            "name": "creative_thinker_generation_user.json",
            "label": "2 · Generation — steered by the researcher's own ideas",
            "description": (
                "Same fan-out, but the context carries the user's own notes, their "
                "hardware constraint and an idea they already rejected. Three chosen "
                "stances instead of the default roster."
            ),
        },
        {
            "name": "creative_thinker_generation_budget.json",
            "label": "3 · Generation — budget cuts the fan-out (truncated)",
            "description": (
                "Five stances asked for, two calls allowed. Returns the whole "
                "candidates it could afford and ends `truncated`, naming the limit — "
                "a partial run must never look like a finished one."
            ),
        },
        {
            "name": "creative_thinker_refinement.json",
            "label": "4 · Refinement — deep dive on what generation produced",
            "description": (
                "Continues example 1: the idea is the candidate the user accepted, "
                "and it still carries that generation run in its `runs[]`. A tree of "
                "depth 3, whose prompts read the earlier run so it does not re-tread "
                "the branches generation already dropped."
            ),
        },
        {
            "name": "creative_thinker_refinement_chain.json",
            "label": "5 · Refinement — depth=1, the chain-of-thought degeneration",
            "description": (
                "The same code path with the depth set to one: a single step, no "
                "tree. This is why there is no third mode."
            ),
        },
        {
            "name": "creative_thinker_refinement_user.json",
            "label": "6 · Refinement — the user's competing idea, argued out",
            "description": (
                "The user wants passes budgeted by entropy, the idea budgets them by "
                "length. Run with the skeptic stance, this is where the selector is "
                "most likely to label a branch as a child or a variant."
            ),
        },
        {
            "name": "creative_thinker_refinement_question.json",
            "label": "7 · Refinement — a question, answered without redesigning anything",
            "description": (
                "The task is a plain question. The run branches over defensible answers, "
                "picks one, and returns it as the envelope's `answer` — the idea itself "
                "may come back barely changed, which is the correct outcome. This is "
                "what makes refinement usable as the session's general work verb."
            ),
        },
        {
            "name": "creative_thinker_merge.json",
            "label": "8 · Merge — two ideas, conflicts recorded not smoothed",
            "description": (
                "Merging is refinement with branches drawn from two source ideas. The "
                "context names both and the conflicts between them; every conflict is "
                "either resolved explicitly or comes back as an open question."
            ),
        },
        {
            "name": "creative_thinker_tool_use.json",
            "label": "9 · Refinement — tool_use on, real Investigator (network)",
            "description": (
                "A branch that needs a fact waits for it instead of inventing it. "
                "Wires the investigator agent at the harness boundary; it searches the "
                "web and stores what it downloads under workspace/dev_creative_thinker/."
            ),
        },
        {
            "name": "creative_thinker_persist.json",
            "label": "10 · Refinement on a real workspace idea, trace persisted",
            "description": (
                "Reads a live record with `ws:<slug>/<idea_id>` (a Phase-1 idea is "
                "normalized on the way in — it has no runs[] yet), thinks, then applies "
                "the envelope so the run lands in that idea.json. The write is the "
                "orchestrator's step, not the agent's, and only the idea is persisted: "
                "drafts and splits still need an explicit accept."
            ),
        },
    ],
    "subagents": [
        {
            "name": "creative_thinker_generate_candidate",
            "run": lambda style="skeptic", context="", idea="": _run_generate_candidate(
                style, context, idea
            ),
            "schema": [
                {"name": "style", "type": "text", "required": True, "placeholder": "skeptic"},
                {"name": "context", "type": "text", "required": False,
                 "placeholder": "the gap / papers, as text"},
                {"name": "idea", "type": "text", "required": False,
                 "placeholder": "the parent idea, as text"},
            ],
        },
    ],
}


def _run_generate_candidate(style: str, context: str, idea: str) -> dict:
    """One styled thinker in isolation — the leaf worth poking at on its own."""
    from .schemas import AgentStyle
    from .subagents.generate_candidate.agent import generate_candidate

    candidate = generate_candidate(
        style=AgentStyle(name=style), context_md=context, idea_md=idea
    )
    return candidate.model_dump()
