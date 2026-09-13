"""Read Phase-1 idea records as the current ``idea.json`` — on the way in only.

The ideas in ``workspace/*/ideas/`` written by the Phase-1 pipeline use
``summary`` / ``method`` / ``related_gaps`` / ``related_papers``, ``status:
"draft"``, and carry no ``runs[]``.

:func:`normalize` maps such a record onto :class:`IdeaRecord`. It is a read-side
adapter: it returns a new object and never writes, so the older files stay as they
are until something deliberately persists over them. A record already in the
current shape passes through unchanged.
"""

from __future__ import annotations

from typing import Any

from src.common.schemas.idea import IdeaRecord

# Phase-1 key -> the section slug it becomes. The spine cannot hold them, and dropping
# them would lose exactly the material the researcher cares about.
_LEGACY_SECTIONS = {
    "summary": "summary",
    "method": "technique",
    "experiments": "experiments",
    "related_papers": "related_papers",
    "short": "short_title",
}

_LEGACY_STATE = {
    "draft": "provisional",
    "accepted": "root",
    "active": "root",
    "derived": "derived",
    "merged": "merge",
    "superseded": "superseded",
}


def is_legacy(raw: dict[str, Any]) -> bool:
    """True when the record predates the format spec (no `runs`, Phase-1 keys)."""
    return "runs" not in raw or any(k in raw for k in ("summary", "method", "related_gaps"))


def normalize(raw: dict[str, Any]) -> IdeaRecord:
    """A Phase-1 or current record → :class:`IdeaRecord`. Never mutates ``raw``."""
    if not is_legacy(raw):
        return IdeaRecord.model_validate(raw)

    data = dict(raw)
    # Mapped keys are dropped rather than carried along: `extra="allow"` would keep them,
    # and a record holding both `status` and `state` (or `related_gaps` and `addresses`)
    # has two sources of truth for the same fact.
    legacy_status = data.pop("status", None)
    legacy_gaps = data.pop("related_gaps", None)
    sections = dict(data.get("sections") or {})
    for legacy_key, slug in _LEGACY_SECTIONS.items():
        value = data.pop(legacy_key, None)
        if value:
            sections[slug] = value

    provenance = list(data.get("provenance") or [])
    if data.get("source"):
        provenance.append({
            "claim": f"Record created by the {data['source']} path.",
            "source": "", "via": str(data.pop("source")),
        })

    # Phase-1 wrote prose per axis; the spec's axes are scores. Keep the prose as a
    # note beside the (still unscored) axis rather than discarding a written judgement.
    evaluation: dict[str, Any] = {}
    for axis, value in (data.get("evaluation") or {}).items():
        if isinstance(value, (int, float)) or value is None:
            evaluation[axis] = value
        else:
            evaluation[f"{axis}_note"] = value

    return IdeaRecord.model_validate({
        **data,
        "state": _LEGACY_STATE.get(str(legacy_status or data.get("state") or ""),
                                   "provisional"),
        "addresses": data.get("addresses") or legacy_gaps or [],
        "sections": sections,
        "evaluation": evaluation,
        "provenance": provenance,
        # The point of normalizing: a legacy idea has no thinking graph. It gets an
        # empty one, and the first creative-thinker run on it fills the first entry.
        "runs": data.get("runs") or [],
    })
