"""apply_edits (LLM) — write the surviving reasoning back, and answer the task.

The one place a refinement run changes the idea's *content*, and the one place it
produces its reply. Both come from the same call because they are the same act of
writing-up: a run asked "would this work for video?" answers in prose and, if the
answer changed the idea, edits it — asking twice would let the two disagree.

The edits arrive as **named operations** and are applied deterministically by
:func:`apply_ops`. A section the model did not mention is still left alone; a
section it wants gone has to be named in a ``delete``. That asymmetry is the point:
growing the record should be easy and shrinking it should be deliberate.
"""

from __future__ import annotations

from typing import Any

from src.agents.creative_thinker.schemas import AgentStyle, IdeaEdit, SectionEdit
from src.common.schemas.idea import IdeaRecord
from src.common.utils.prompts import load_prompt
from src.llm.registry import chat_with_structured_output, get_llm

_SYSTEM = load_prompt(__file__)
_FAST_WRITEBACK_MAX_TOKENS = 6_000

# No section is protected. ``open_questions`` used to be: the next run reads it by
# name, so deleting or renaming it was blocked. But the next run reading it and
# finding nothing is the correct outcome of a run that decided nothing was open —
# and a section the record cannot lose is one the record has to carry when it is
# wrong. It is a section like any other, here and in the UI.


def apply_edits(*, style: AgentStyle, idea_md: str, path_md: str, task: str = "",
                findings: str = "", fast: bool = False) -> IdeaEdit:
    """One model call → the patch to apply, and the answer to the task.

    A one-pass refinement uses Flash at low effort and a bounded completion. Deeper
    refinement retains its requested reasoner because its writeback must reconcile a
    multi-round path. ``IdeaEdit`` is already a patch contract: unchanged spine fields
    stay empty and only named section operations are returned.
    """
    sections = [f"# The idea as it stands\n\n{idea_md}"]
    if task:
        sections.append(f"# What this run was asked to do\n\n{task}")
    sections.append(
        f"# The surviving branch of reasoning\n\n"
        f"{path_md or '_(no branch survived — nothing was decided this run)_'}"
    )
    if findings:
        sections.append(f"# Verified findings\n\n{findings}")
    model = "cheap" if fast else style.model
    return chat_with_structured_output(
        get_llm(
            model,
            style.temperature,
            reasoning_effort="low" if fast else None,
            max_tokens=_FAST_WRITEBACK_MAX_TOKENS if fast else None,
        ),
        _SYSTEM,
        "\n\n".join(sections),
        IdeaEdit,
    )


def _slug(key: str) -> str:
    return str(key or "").strip().lower().replace(" ", "_")


# The spine has its own fields on the edit, so a section by the same name is the
# model writing one thing into two channels. Seen live: a run asked to rewrite the
# description filled ``description`` *and* emitted ``set description``, leaving the
# record with the text in the spine and a duplicate section shadowing it — which
# then renders twice and drifts apart on the next run. The spine field wins;
# ``sections`` is the *open* part, and these four names are not open.
_SPINE_SLUGS = frozenset({"title", "one_liner", "description", "problem"})


def apply_ops(sections: dict[str, Any], edits: list[SectionEdit]) -> dict[str, Any]:
    """Run the ops over a copy of ``sections`` and return the result.

    Order is preserved (and ``reorder`` is what changes it), because the section
    order *is* the document's structure — the researcher reads it top to bottom.

    Unknown keys are not an error: a ``delete`` or ``rename`` naming a section that
    is not there is a no-op, since the outcome the model asked for already holds.
    """
    out = dict(sections)
    for edit in edits:
        key = _slug(edit.key)
        if edit.op == "reorder":
            wanted = [_slug(k) for k in edit.order if _slug(k) in out]
            out = {**{k: out[k] for k in wanted},
                   **{k: v for k, v in out.items() if k not in wanted}}
            continue
        if not key or key in _SPINE_SLUGS:
            continue
        if edit.op == "set":
            if str(edit.value).strip():
                out[key] = edit.value
        elif edit.op == "append":
            out[key] = _append(out.get(key), edit.value)
        elif edit.op == "delete":
            out.pop(key, None)
        elif edit.op == "rename":
            target = _slug(edit.to)
            if target in _SPINE_SLUGS:
                continue
            if target and key in out:
                out = {(target if k == key else k): v for k, v in out.items()}
    return out


def _append(existing: Any, value: Any) -> Any:
    """Append to a list section, or to markdown prose, without flattening either."""
    if not str(value).strip():
        return existing
    if existing is None or not str(existing).strip():
        return value
    if isinstance(existing, list):
        return existing + (value if isinstance(value, list) else [value])
    if isinstance(value, list):
        return str(existing) + "\n" + "\n".join(f"- {v}" for v in value)
    return f"{existing}\n\n{value}"


def merge_edit(idea: IdeaRecord, edit: IdeaEdit) -> IdeaRecord:
    """Apply the edit to the idea copy — deterministic, and never to the spine.

    ``description`` and ``problem`` are rewritten only when the model actually wrote
    them; the rest of the spine (title, gaps addressed, relations) is untouched here,
    and ``runs[]`` is appended elsewhere. Open questions are stored in the
    ``open_questions`` section so they survive into the next run's prompt.

    ``problem`` additionally never leaves here empty. The prompt asks for it on every
    run — restated when the run did not sharpen it — so an empty one means the model
    skipped a spine field rather than that the idea has no problem; the one-liner
    stands in until a later run writes a real one.
    """
    if edit.description.strip():
        idea.description = edit.description.strip()
    if edit.problem.strip():
        idea.problem = edit.problem.strip()
    if not (idea.problem or "").strip():
        idea.problem = (idea.one_liner or "").strip()
    sections = apply_ops(dict(idea.sections or {}), list(edit.edits or []))
    if edit.open_questions:
        sections["open_questions"] = list(edit.open_questions)
    idea.sections = sections
    # The order the ops produced, written down. ``apply_ops`` keeps it in the map's
    # key order, but the record travels through JSON and through agents that rebuild
    # the map — an order that exists only as key order is not a change anyone can
    # see, diff, or preserve. ``section_order`` is where the design of record puts it.
    idea.section_order = list(sections)
    return idea
