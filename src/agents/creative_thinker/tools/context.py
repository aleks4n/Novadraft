"""Turning the plan's loaded context into prompt text — deterministically.

The thinker does not decide what to load; the plan does. This module only
*renders* what arrived, and records which ids were in view so the run header says
exactly what the run could see.

It also renders **prior ground**: what earlier runs on this idea already tried. That
is the whole reason ``runs[]`` is append-only — the next run reads it so it does not
re-tread the same branches.

How much of it is rendered matters more than it looks. ``runs[]`` grows forever, so
pasting every pruned branch into the prompt means run twenty is mostly a recitation
of runs one through nineteen and the idea itself is a footnote. Older runs are
therefore rendered from their :class:`RunSummary` — one paragraph each — and only the
most recent runs contribute raw branch text, where the detail is still worth its
tokens.

Recognized context keys (all optional, all lists of dicts): ``gaps``, ``papers``,
``ideas``, ``discussion``, ``user_input``, ``documents``.
"""

from __future__ import annotations

from typing import Any

from src.common.schemas.idea import IdeaRecord

_ID_KEYS = ("gap_id", "paper_id", "idea_id", "id")
_TITLE_KEYS = ("title", "name", "canonical_description")
_BODY_KEYS = ("description", "summary", "text", "content", "one_liner")

_MAX_ITEMS = 12
_MAX_BODY_CHARS = 1200
_MAX_PRIOR_NODES = 12  # raw branches, from the recent runs only
_VERBATIM_RUNS = 2  # how many recent runs contribute raw branch text
_MAX_SUMMARIES = 12


def _as_items(value: Any) -> list[dict]:
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return [v if isinstance(v, dict) else {"text": str(v)} for v in value]
    if isinstance(value, str):
        return [{"text": value}]
    return []


def _pick(item: dict, keys: tuple[str, ...]) -> str:
    return next((str(item[k]) for k in keys if item.get(k)), "")


def context_ids(context: dict[str, Any]) -> list[str]:
    """Every addressable id the run had in view, for the run header."""
    ids: list[str] = []
    for key, value in (context or {}).items():
        for item in _as_items(value):
            found = _pick(item, _ID_KEYS)
            if found:
                ids.append(found)
            elif key in ("user_input", "discussion"):
                ids.append(key)
    seen: set[str] = set()
    return [i for i in ids if not (i in seen or seen.add(i))]


def render_context(context: dict[str, Any]) -> str:
    """The loaded context as markdown, capped so one huge PDF cannot crowd out
    the gap the run is actually about."""
    blocks: list[str] = []
    for key, value in (context or {}).items():
        items = _as_items(value)[:_MAX_ITEMS]
        if not items:
            continue
        lines = [f"## {key}"]
        for item in items:
            ident = _pick(item, _ID_KEYS)
            title = _pick(item, _TITLE_KEYS)
            body = _pick(item, _BODY_KEYS)[:_MAX_BODY_CHARS]
            head = " — ".join(p for p in (ident, title) if p)
            lines.append(f"- **{head or 'item'}**: {body}" if body else f"- **{head}**")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) or "_(no context loaded)_"


def existing_idea_texts(context: dict[str, Any]) -> list[str]:
    """Ideas already in the workspace slice, as text — the second thing a
    generation batch is deduplicated against."""
    out = []
    for item in _as_items((context or {}).get("ideas")):
        parts = [_pick(item, _TITLE_KEYS), _pick(item, _BODY_KEYS)]
        text = " ".join(p for p in parts if p).strip()
        if text:
            out.append(text)
    return out


def render_idea(idea: IdeaRecord) -> str:
    """The idea under work, spine + open sections, as markdown."""
    lines = [f"# {idea.title or '(untitled idea)'}"]
    if idea.one_liner:
        lines.append(f"_{idea.one_liner}_")
    if idea.problem:
        lines.append(f"\n## the problem it addresses\n{idea.problem}")
    if idea.description:
        lines.append(f"\n## description\n{idea.description}")
    if idea.addresses:
        # Its own heading, not a loose line under ``## description``. Without one it
        # reads as the last paragraph of the description, and the write-up copies it
        # back into the description field — seen live, twice, in the demo runs.
        lines.append(f"\n## addresses gaps\n{', '.join(idea.addresses)}")
    for key, value in (idea.sections or {}).items():
        rendered = "\n".join(f"- {v}" for v in value) if isinstance(value, list) else str(value)
        lines.append(f"\n## {key}\n{rendered}")
    return "\n".join(lines)


def run_summaries(idea: IdeaRecord) -> str:
    """Every earlier run in a paragraph each — the idea's memory of its own history.

    This is what makes the accumulated graph usable rather than merely stored: a
    reader (model or human) gets what each run set out to do, what it settled and
    what it dropped, without reading the branches themselves.
    """
    blocks: list[str] = []
    for entry in idea.runs:
        summary = entry.outcome.summary
        header = f"**{entry.run_id}** ({entry.header.mode}"
        if entry.timestamp:
            header += f", {entry.timestamp[:10]}"
        header += ")"
        if summary.is_empty():
            # A run written before summaries existed, or one that produced nothing:
            # its status is still worth a line — silence would read as "never happened".
            blocks.append(f"- {header} — {entry.outcome.status}"
                          f"{': ' + entry.outcome.reason if entry.outcome.reason else ''}")
            continue
        parts = [f"- {header}"]
        if summary.objective:
            parts.append(f"  - asked: {summary.objective}")
        if summary.accepted:
            parts.append(f"  - settled: {summary.accepted}")
        if summary.rejected:
            parts.append(f"  - dropped: {summary.rejected}")
        for question in summary.open[:3]:
            parts.append(f"  - still open: {question}")
        # A standing proposal, so this run can see the change was already put to the
        # user rather than re-deriving it and suggesting it a second time.
        for proposal in entry.outcome.proposed_changes[:3]:
            parts.append(f"  - proposed, not applied: {proposal}")
        blocks.append("\n".join(parts))
    if not blocks:
        return "_(no earlier runs on this idea)_"
    return "\n".join(blocks[-_MAX_SUMMARIES:])


def prior_ground(idea: IdeaRecord) -> str:
    """What earlier runs already tried and dropped — so this run does not repeat it.

    Two resolutions, deliberately. Every run contributes its **summary**; only the
    last :data:`_VERBATIM_RUNS` also contribute their raw ``pruned`` / ``rejected``
    branch text. Recent dead ends are worth their tokens because this run is likely
    to re-derive them; a dead end from eight runs ago is not, and the summary already
    records that it happened.

    Kept and accepted thinking is deliberately absent from both: it is already
    visible in the description and sections, which the prompt renders separately.
    """
    blocks = [f"## Earlier runs\n{run_summaries(idea)}"]

    lines: list[str] = []
    for entry in list(idea.runs)[-_VERBATIM_RUNS:]:
        for node in entry.nodes:
            if node.status in ("pruned", "rejected"):
                why = f" ({node.comparison})" if node.comparison else ""
                lines.append(f"- [{node.status}, {node.style}] {node.text[:220]}{why}")
    if lines:
        blocks.append("## Branches the most recent runs dropped\n"
                      + "\n".join(lines[-_MAX_PRIOR_NODES:]))
    return "\n\n".join(blocks)
