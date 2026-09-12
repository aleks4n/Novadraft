"""expand_branch (LLM) — one level of the refinement tree.

Called once per depth level on the surviving branch: given the idea, **the task the
run was set**, and the path so far, propose the next siblings.

The task is what this branches on. Earlier it branched on whatever the idea had
stored under ``open_questions``, which made refinement useless for anything a
session actually asks — you cannot answer "would this work for video?" by
expanding the idea's own navel-gazing. Open questions are still supplied, but as
*material*, not as the agenda.

The tree's *shape* — how wide, how deep, how many survive — is decided by the graph
and the budget ledger, not here.
"""

from __future__ import annotations

from src.agents.creative_thinker.schemas import AgentStyle, Branch, BranchSet
from src.agents.creative_thinker.styles import load_style_prompt
from src.common.utils.prompts import load_prompt
from src.llm.registry import chat_with_structured_output, get_llm

_TASK = load_prompt(__file__)
_MAX_CONTEXT_CHARS = 24_000
_MAX_PRIOR_CHARS = 8_000

# Wider than this and the selection becomes a survey rather than a choice: the
# evaluator has to hold every branch side by side in one call, and past six it stops
# comparing and starts summarising. ``width`` is clamped here rather than validated
# at the boundary so a plan asking for more still runs, just narrower.
MAX_BRANCHES = 6


def expand_branch(*, style: AgentStyle, idea_md: str, path_md: str,
                  open_questions: list[str], task: str = "", width: int = 3,
                  context_md: str = "", prior_ground: str = "",
                  findings: str = "") -> list[Branch]:
    """One reasoner call → the next siblings (at most ``width``, hard cap
    :data:`MAX_BRANCHES`).

    ``findings`` carries answers the Investigator already returned on this path,
    so a branch that asked for a fact last round can use it this round instead of
    asking again.
    """
    wanted = max(1, min(int(width or 1), MAX_BRANCHES))
    context_md = context_md[:_MAX_CONTEXT_CHARS]
    prior_ground = prior_ground[-_MAX_PRIOR_CHARS:]
    sections = [
        f"# The idea under refinement\n\n{idea_md}",
        f"# What this run was asked to do\n\n{task or '_(no explicit task — develop the idea where it is weakest)_'}",
        f"# The branch reached so far\n\n{path_md or '_(root — no branch taken yet)_'}",
        "# Open questions recorded on the idea\n\n" + (
            "\n".join(f"- {q}" for q in open_questions) if open_questions
            else "_(none stated)_"
        ),
        f"# How many branches are wanted\n\nUp to **{wanted}**. Fewer is correct when "
        f"there are fewer honest alternatives.",
    ]
    if findings:
        sections.append(f"# Verified findings from the Investigator\n\n{findings}")
    if context_md:
        sections.append(f"# Loaded context\n\n{context_md}")
    sections.append(f"# Already tried in earlier runs\n\n{prior_ground or '_(nothing)_'}")

    result = chat_with_structured_output(
        get_llm(style.model, style.temperature,
                reasoning_effort="low" if style.model == "cheap" else None),
        f"{load_style_prompt(style.name)}\n\n---\n\n{_TASK}",
        "\n\n".join(sections),
        BranchSet,
    )
    branches = [b for b in (result.branches or []) if (b.text or b.focus).strip()]
    return branches[:wanted]
