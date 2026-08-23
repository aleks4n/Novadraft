"""generate_candidate (LLM) — one styled thinker, one rough candidate.

The generation fan-out calls this ``n`` times, once per style, with no shared
state: each call sees the same brief and its own stance, which is what makes the
batch diverse in a way temperature cannot.
"""

from __future__ import annotations

from src.agents.creative_thinker.schemas import AgentStyle, Candidate
from src.agents.creative_thinker.styles import load_style_prompt
from src.common.utils.prompts import load_prompt
from src.llm.registry import chat_with_structured_output, get_llm

_TASK = load_prompt(__file__)


def _system(style: AgentStyle) -> str:
    """Stance first, task second — the stance has to constrain how the task is read."""
    return f"{load_style_prompt(style.name)}\n\n---\n\n{_TASK}"


def generate_candidate(*, style: AgentStyle, context_md: str, idea_md: str = "",
                       prior_ground: str = "", avoid: list[str] | None = None) -> Candidate:
    """One reasoner call → one :class:`Candidate`.

    ``avoid`` holds the ideas already in the workspace slice and ``prior_ground``
    the branches earlier runs dropped; both go into the prompt so the model does
    not spend its slot on something already on record. Neither is trusted to work
    — the deterministic dedup pass runs regardless.
    """
    avoid = avoid or []
    sections = [f"# Loaded context\n\n{context_md}"]
    if idea_md:
        sections.append(f"# Parent idea this should branch from\n\n{idea_md}")
    if avoid:
        listed = "\n".join(f"- {a[:300]}" for a in avoid)
        sections.append(f"# Ideas that already exist — do not re-propose\n\n{listed}")
    sections.append(f"# Already tried in earlier runs\n\n{prior_ground or '_(nothing)_'}")

    candidate = chat_with_structured_output(
        get_llm(style.model, style.temperature),
        _system(style),
        "\n\n".join(sections),
        Candidate,
    )
    return _ensure_usable(candidate, style, context_md)


def _ensure_usable(candidate: Candidate, style: AgentStyle, context_md: str) -> Candidate:
    """Keep an empty model response from collapsing the whole batch into one node.

    The offline mock model (no API key configured) returns a defaults-only
    object; without this every style would produce byte-identical text, dedup
    would fold them into a single candidate, and a dev run would look broken
    rather than un-configured. The filler is clearly labelled as such.
    """
    if (candidate.title or candidate.description).strip():
        return candidate
    head = context_md.strip().splitlines()[0][:120] if context_md.strip() else "the loaded gap"
    return candidate.model_copy(update={
        "title": f"[{style.name}] placeholder candidate",
        "one_liner": f"Placeholder produced without a model — stance: {style.name}.",
        "description": (
            f"No model output was available, so this is a placeholder from the "
            f"{style.name} stance on: {head}."
        ),
        "problem": f"Placeholder — the run had no model, so the problem it attacks on {head} was never stated.",
        "rationale": "placeholder — no model configured",
    })
