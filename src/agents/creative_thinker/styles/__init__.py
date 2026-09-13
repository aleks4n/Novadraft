"""The agent-style roster — diversity expressed as prompts.

A style is a way of thinking: a stance, its preferred moves, what it considers a
good idea, and what it refuses to do. Each lives in its own ``<name>.md`` beside
this file, and a style may carry its own model and decoding settings
(:class:`AgentStyle`).

Every thought node records the style that produced it. Generation fans out over
distinct styles (:func:`pick_styles`); refinement runs one, usually ``neutral``.
"""

from __future__ import annotations

from pathlib import Path

from src.agents.creative_thinker.schemas import AgentStyle

_DIR = Path(__file__).resolve().parent

# The starting roster, in fan-out order. Ordered so a small ``n`` still spans very
# different stances: improve it → fuse two → attack its premise → import from
# elsewhere → strip it down → break a constraint.
ROSTER: tuple[str, ...] = (
    "optimizer", "combiner", "skeptic", "transferer", "minimalist", "extremist",
)

DEFAULT_REFINEMENT_STYLE = "neutral"


def available() -> list[str]:
    return sorted(p.stem for p in _DIR.glob("*.md"))


def load_style_prompt(name: str) -> str:
    """The stance prompt for ``name``; falls back to ``neutral`` if unknown, so a
    plan naming a style that has not been written yet still runs."""
    path = _DIR / f"{name}.md"
    if not path.exists():
        path = _DIR / f"{DEFAULT_REFINEMENT_STYLE}.md"
    return path.read_text(encoding="utf-8")


def pick_styles(requested: list[AgentStyle], n: int) -> list[AgentStyle]:
    """The ``n`` styles a generation fan-out will use.

    The plan's styles come first and are never dropped. If the plan asked for
    fewer than ``n``, the roster fills the rest in order, skipping duplicates; if
    ``n`` exceeds the roster, styles repeat — a second ``optimizer`` still writes
    a different candidate, it just stops adding stance diversity.
    """
    picked = list(requested[:n])
    if len(picked) >= n:
        return picked
    taken = {s.name for s in picked}
    for name in ROSTER:
        if len(picked) >= n:
            break
        if name not in taken:
            picked.append(AgentStyle(name=name))
            taken.add(name)
    i = 0
    while len(picked) < n:  # n > roster size: cycle rather than shrink the fan-out
        picked.append(AgentStyle(name=ROSTER[i % len(ROSTER)]))
        i += 1
    return picked


def refinement_style(requested: list[AgentStyle]) -> AgentStyle:
    """Depth uses one style — the plan's first, or ``neutral``."""
    return requested[0] if requested else AgentStyle(name=DEFAULT_REFINEMENT_STYLE)
