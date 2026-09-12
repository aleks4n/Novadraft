"""The workspace index — what exists, by id and title, and nothing more.

Context assembly answers "what is this turn *about*": the selected gaps and ideas, one
hop out, in full. This module answers the other question — "what else is there at all" —
and the two must not be confused. A step that only ever sees its own slice cannot know
that the workspace holds thirty other gaps, so it never thinks to ask for one; a step
handed those thirty gaps in full has no room left for the one it is working on.

So a catalog entry is a **pointer, not a record**: an id, a title, and one short marker
(a gap's stage, a paper's year). No description, no evidence, no summary. The entry is
big enough to recognise something by and too small to reason from — which is the point.
When a step wants the record behind an entry it asks the investigator by id, and that
call is a visible step rather than a silent widening of scope.

Cost is what forces this. The `transformers` workspace holds 106 gaps; at a title each
that is ~1.3k tokens, and at a description each it would be ten times that and would
crowd out the gap the run is actually about.

Reading 120 files to build one index is cheap in wall-clock but not free, and a turn
rebuilds it repeatedly. Entries are therefore memoised per file by `(path, mtime, size)`:
a file that has not changed since the last build is not re-read, and one that has is
picked up without any explicit invalidation. The cache lives in the process, so a fresh
run always starts from disk and there is no stale index file to garbage-collect.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from src.common.utils import storage

# Per-kind ceilings. A workspace can grow past what is worth listing, and a truncated
# list that says so is honest where a silently cut one is not — the caller appends a
# remainder line naming what it dropped (see `Catalog.overflow`).
MAX_GAPS = 60
MAX_PAPERS = 40
MAX_IDEAS = 40

# Gaps are listed most-actionable first, so a truncated list keeps the live ones. `master`
# holds validated, ranked opportunities; `archive` holds gaps that are settled or merged
# away and is the first thing a ceiling should cut.
_STAGE_ORDER: dict[str, int] = {
    "master": 0, "to_rank": 1, "to_validate": 2, "pending": 3, "archive": 4,
}

_TITLE_CHARS = 140  # a title is a handle, not a sentence to reason from

CatalogKind = Literal["gap", "paper", "idea"]

# (path, mtime_ns, size) -> entry. Keyed by the file's identity *and* its state, so a
# changed file misses the cache rather than needing to be evicted from it.
_ENTRY_CACHE: dict[tuple[str, int, int], dict[str, Any]] = {}


def _clip(text: object) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= _TITLE_CHARS else text[: _TITLE_CHARS - 1] + "…"


def _cached(path: Path) -> dict[str, Any] | None:
    """The entry built from this file last time, if the file is byte-identical since.

    `st_mtime_ns` and `st_size` together are what a rebuild is keyed on. Neither alone is
    enough: an edit that keeps the length changes only the mtime, and a filesystem with
    coarse timestamps can write two different records within one tick.
    """
    try:
        stat = path.stat()
    except OSError:
        return None
    return _ENTRY_CACHE.get((str(path), stat.st_mtime_ns, stat.st_size))


def _store(path: Path, entry: dict[str, Any]) -> dict[str, Any]:
    try:
        stat = path.stat()
    except OSError:
        return entry
    _ENTRY_CACHE[(str(path), stat.st_mtime_ns, stat.st_size)] = entry
    return entry


def _read(path: Path) -> dict | None:
    """A record off disk, or None. A file that will not parse is skipped rather than
    raised on: one prototype-era record must not cost the whole index."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


# --- entries ------------------------------------------------------------------


def _gap_entry(category_slug: str, gap_id: str, stage: str) -> dict[str, Any] | None:
    path = storage.gap_json_path(category_slug, gap_id, stage)  # type: ignore[arg-type]
    if (hit := _cached(path)) is not None:
        return hit
    raw = _read(path)
    if raw is None:
        return None
    # `canonical_description` is the fallback because an older record may carry no title,
    # and an entry rendered as a bare id is unrecognisable — which defeats the index.
    title = raw.get("title") or raw.get("canonical_description") or ""
    return _store(path, {
        "kind": "gap",
        "id": gap_id,
        "title": _clip(title),
        "marker": stage,
        "type": raw.get("type") or "",
    })


def _paper_entry(category_slug: str, paper_id: str) -> dict[str, Any] | None:
    path = storage.paper_json_path(category_slug, paper_id)
    if (hit := _cached(path)) is not None:
        return hit
    raw = _read(path)
    if raw is None:
        return None
    metadata = raw.get("metadata") or {}
    year = metadata.get("year")
    return _store(path, {
        "kind": "paper",
        "id": paper_id,
        "title": _clip(metadata.get("title") or ""),
        "marker": str(year) if year else "",
        "type": "",
    })


def _idea_entry(category_slug: str, idea_id: str) -> dict[str, Any] | None:
    path = storage.ideas_dir(category_slug) / f"{idea_id}.json"
    if (hit := _cached(path)) is not None:
        return hit
    raw = _read(path)
    if raw is None:
        return None
    # `one_liner` before `title`: an idea's one-liner is the more recognisable handle, and
    # older records written by the earlier pipeline carry no title at all.
    title = raw.get("title") or raw.get("one_liner") or ""
    return _store(path, {
        "kind": "idea",
        "id": idea_id,
        "title": _clip(title),
        "marker": "",
        "type": "",
    })


# --- the index ----------------------------------------------------------------


def gap_entries(category_slug: str) -> list[dict[str, Any]]:
    """Every gap in the workspace, live stages first, `archive` last."""
    out: list[dict[str, Any]] = []
    for stage in storage.GAP_STAGES:
        for gap_id in storage.list_gap_ids(category_slug, stage):
            if entry := _gap_entry(category_slug, gap_id, stage):
                out.append(entry)
    out.sort(key=lambda e: _STAGE_ORDER.get(e.get("marker", ""), 9))
    return out


def paper_entries(category_slug: str) -> list[dict[str, Any]]:
    return [e for pid in storage.list_paper_ids(category_slug)
            if (e := _paper_entry(category_slug, pid))]


def idea_entries(category_slug: str) -> list[dict[str, Any]]:
    return [e for iid in storage.list_idea_ids(category_slug)
            if (e := _idea_entry(category_slug, iid))]


def build(category_slug: str, *, exclude_ids: set[str] | None = None) -> dict[str, Any]:
    """The whole index for one workspace, capped per kind.

    `exclude_ids` drops entries whose record is already in the caller's context in full.
    Without it the same gap appears twice — once as the thing the run is about and once
    as a title in the index — and a model reading both has to work out that they are one
    record, which it sometimes gets wrong and writes about as two.

    Returns `{"gaps": [...], "papers": [...], "ideas": [...], "counts": {...},
    "omitted": {...}}`. `counts` is the true total per kind and `omitted` is how many a
    ceiling dropped, both stated outright so a reader never has to infer completeness
    from the length of a list.
    """
    skip = exclude_ids or set()
    kinds = (("gaps", gap_entries, MAX_GAPS),
             ("papers", paper_entries, MAX_PAPERS),
             ("ideas", idea_entries, MAX_IDEAS))

    index: dict[str, Any] = {"counts": {}, "omitted": {}}
    for key, load, ceiling in kinds:
        entries = [e for e in load(category_slug) if e["id"] not in skip]
        index[key] = entries[:ceiling]
        index["counts"][key] = len(entries)
        index["omitted"][key] = max(0, len(entries) - ceiling)
    return index


def is_empty(index: dict[str, Any] | None) -> bool:
    """True when the index names nothing — a fresh workspace, or one where every record
    is already in context. The renderer drops the section entirely rather than printing a
    heading over nothing."""
    if not index:
        return True
    return not any(index.get(k) for k in ("gaps", "papers", "ideas"))
