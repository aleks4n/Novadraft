"""How a run's results are shown to the steps that judge or replan on them.

`context.summarise` does this for what was loaded; this is its counterpart for what came
back. Both exist so no LLM step ever sees raw `model_dump()` JSON.

The previous version dumped that JSON truncated at 6000 chars. One five-idea result
serialises to ~9500, so reflect saw three ideas of five, reported a shortfall that did not
exist, and looped to fix it. The fix is not a bigger cap: nothing is cut, the count is
stated outright so it never has to be inferred by reading, and the fields that are large
and unjudgeable are dropped by name instead of by character position.
"""

from __future__ import annotations

from src.common.schemas.idea import Idea
from src.agents.orchestrator_agent.schemas import ToolCall, ToolResult


def _idea_block(idea: Idea) -> list[str]:
    """One draft, in full.

    `thought_graph` is left out: the thinker owns its shape, it is unbounded, and no
    structural criterion can be stated over it. `idea_id`, `status`, `origin` and the
    timestamps are left out too — they are the same on every draft, and a step that never
    sees an id cannot echo a hallucinated one back.
    """
    lines = [f'  · "{idea.title}"']
    for text in (idea.short, idea.description):
        if text:
            lines.append(f"      {text}")
    for step in idea.method:
        lines.append(f"      - {step}")
    if idea.related_papers:
        lines.append(f"      papers: {', '.join(idea.related_papers)}")
    if idea.parents:
        lines.append("      addresses: " + ", ".join(
            f"{edge.parent_kind} {edge.parent_id}" for edge in idea.parents))
    return lines


def _args_brief(step: ToolCall) -> str:
    """The step's arguments, with `instruction` pulled out onto its own line below."""
    bits = [f"{k}={v!r}" for k, v in step.args.items() if k != "instruction"]
    if step.needs:
        bits.append(f"needs={step.needs}")
    return ", ".join(bits)


def _block(result: ToolResult, step: ToolCall | None, label: str) -> list[str]:
    lines = [label]
    if step is not None:
        if brief := _args_brief(step):
            lines.append(f"    {brief}")
        if instruction := step.args.get("instruction"):
            lines.append(f"    instruction: {instruction}")
    if not result.ok:
        lines.append(f"    FAILED — {result.error}")
        return lines
    lines.append(f"    returned {len(result.ideas)} idea(s)")
    for idea in result.ideas:
        lines.extend(_idea_block(idea))
    if result.text:
        lines.append(f"    text: {result.text}")
    return lines


def results_view(steps: list[ToolCall], results: list[ToolResult], carried: int = 0) -> str:
    """What ran and what came back, for reflect to judge and for the planner to replan on.

    `carried` is how many of `results` were kept from earlier rounds — those have no step
    in this plan, so they are shown separately rather than mispaired by index.
    """
    if not results:
        return "- (no tools ran)"

    total = sum(len(r.ideas) for r in results)
    lines = [f"{total} candidate idea(s) in hand in total, across every step below.", ""]

    earlier, this_round = results[:carried], results[carried:]
    if earlier:
        lines.append(f"Kept from earlier rounds — already offered, do not produce again:")
        for result in earlier:
            lines.extend(_block(result, None, f"  {result.tool}"))
        lines += ["", "This round:"]

    for i, result in enumerate(this_round, 1):
        step = steps[i - 1] if i - 1 < len(steps) else None
        lines.extend(_block(result, step, f"step {i}  {result.tool}"))
    return "\n".join(lines)
