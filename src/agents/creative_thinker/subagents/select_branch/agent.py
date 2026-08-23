"""select_branch (LLM) — the refinement evaluator, in exactly one call.

Given the idea, the task the run was set, and every sibling produced by one
expansion, decide which single branch to deepen — or say that you cannot.

This replaced a round-robin of pairwise votes. Three reasons it is one call now:

- **Cost.** ``k`` siblings cost ``k(k-1)/2`` comparisons; the same judgement is
  available from one call that sees all of them at once.
- **Honesty.** Round-robin arithmetic can crown a winner no single comparison ever
  endorsed — a → b, b → c, c → a resolves to a tie the model never expressed.
- **Shape.** Only a call that looks at the whole frontier can tell three facets of
  one idea from three genuinely different ideas, which is exactly the distinction
  that decides whether an undecided round becomes "pick one" or "make a variant of
  each".

The model is still only ever asked a *relative* question. It never scores a branch.
"""

from __future__ import annotations

from src.agents.creative_thinker.schemas import AgentStyle, BranchSelection
from src.common.utils.prompts import load_prompt
from src.llm.registry import chat_with_structured_output, get_llm

_SYSTEM = load_prompt(__file__)


def select_branch(*, style: AgentStyle, idea_md: str, texts: list[str],
                  titles: list[str] | None = None, task: str = "",
                  context_md: str = "") -> BranchSelection:
    """One reasoner call → the decision over the whole frontier.

    ``titles`` only label the branches in the prompt; the decision is returned as an
    index so it cannot be lost to a re-worded title.
    """
    titles = titles or []
    listed = "\n\n".join(
        f"## Branch {i}{f' — {titles[i]}' if i < len(titles) and titles[i] else ''}\n\n{text}"
        for i, text in enumerate(texts)
    )
    sections = [f"# The idea being refined\n\n{idea_md}"]
    if task:
        sections.append(f"# What this run was asked to do\n\n{task}")
    sections.append(f"# The branches to choose between\n\n{listed}")
    if context_md:
        sections.append(f"# Loaded context\n\n{context_md}")

    selection = chat_with_structured_output(
        get_llm(style.model, style.temperature), _SYSTEM, "\n\n".join(sections),
        BranchSelection,
    )
    return _clamp(selection, len(texts))


def _clamp(selection: BranchSelection, count: int) -> BranchSelection:
    """An out-of-range winner is an undecided run, never branch 0.

    A model that answers ``winner: 5`` for three branches has not chosen anything.
    Clamping into range would invent a decision; falling back to *undecided* keeps
    every branch alive and hands the choice to the person who can actually make it,
    which is the same thing the honest tie does.
    """
    if selection.winner is not None and not 0 <= selection.winner < count:
        return selection.model_copy(update={
            "winner": None,
            "undecided": True,
            "reasoning": (
                f"the evaluator named branch {selection.winner}, which does not exist "
                f"among {count} — treated as undecided. "
                f"{selection.reasoning}"
            ).strip(),
        })
    return selection
