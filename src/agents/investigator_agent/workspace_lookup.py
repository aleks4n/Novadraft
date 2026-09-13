"""Reading the workspace — the investigator's fourth source, beside papers, web and URLs.

A read surface only: no writes, no staging, no promotion. Two ways in:

- **By id** — the asker saw the id in a catalog entry and wants the record behind
  it.
- **By query** — scored lexically over titles and descriptions rather than by
  embeddings; the corpus is a few hundred short records and the terms are the
  user's own.

What comes back is text for an LLM to answer from, not records for code to
consume.
"""
from __future__ import annotations

import re
from typing import Any, Literal

from src.common.tools import paper_access
from src.common.utils import storage

Kind = Literal["gap", "paper", "idea"]

_MAX_RECORDS = 6  # records rendered into one answer prompt
_MAX_SECTION_CHARS = 12_000  # one named paper section; a long one still fits a prompt
_MAX_MATCHES = 12  # candidates scored down to _MAX_RECORDS

# Ids are self-describing in this workspace, which is what makes a bare id resolvable
# without being told what kind of thing it is. Anything unrecognised is tried as all three.
_ID_PREFIXES: dict[str, Kind] = {"gap_": "gap", "arxiv_": "paper", "doi_": "paper",
                                 "hash_": "paper", "idea_": "idea"}

_STOPWORDS = frozenset(
    "a an and are as at be by for from has have how in is it its of on or that the to "
    "was what when where which who why with does do can could would should about".split()
)


def _terms(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", (text or "").lower())
            if len(t) > 2 and t not in _STOPWORDS}


# --- rendering one record -----------------------------------------------------


def _render_gap(record: dict) -> str:
    lines = [f"GAP {record.get('gap_id', '?')}"
             + (f"  [{record['type']}]" if record.get("type") else "")]
    for key in ("title", "canonical_description", "description"):
        if value := record.get(key):
            lines.append(f"{key}: {value}")
    for item in record.get("evidence") or []:
        if not isinstance(item, dict):
            continue
        where = " / ".join(str(x) for x in (item.get("paper_id"), item.get("section")) if x)
        if quote := (item.get("quote") or item.get("anchor")):
            lines.append(f'evidence ({where}): "{quote}"')
    verdict = record.get("verdict") or {}
    if isinstance(verdict, dict) and verdict.get("status"):
        lines.append(f"verdict: {verdict['status']} — {verdict.get('reasoning', '')}")
    ranking = record.get("ranking") or {}
    if isinstance(ranking, dict) and ranking:
        scores = ", ".join(f"{k}={v}" for k, v in ranking.items()
                           if isinstance(v, (int, float)))
        if scores:
            lines.append(f"ranking: {scores}")
    return "\n".join(lines)


def _render_paper(category_slug: str, paper_id: str, record: dict, section: str) -> str:
    """A paper as metadata + structured summary, plus one named section when asked for.

    The full text is not rendered and the section is: a paper runs ~60k chars and a
    question about one is nearly always a question about one part of it. Asking for
    "the experiments" and getting the whole paper is how a prompt fills up with material
    nobody asked about.
    """
    metadata = record.get("metadata") or {}
    lines = [f"PAPER {paper_id}",
             f"title: {metadata.get('title') or '(untitled)'}"]
    if year := metadata.get("year"):
        lines.append(f"year: {year}")
    if venue := metadata.get("venue"):
        lines.append(f"venue: {venue}")
    if abstract := metadata.get("abstract"):
        lines.append(f"abstract: {abstract}")

    summary = record.get("summary")
    if isinstance(summary, dict):
        for key, value in summary.items():
            if not value:
                continue
            lines.append(f"{key}: {value}")

    sections = paper_access.get_sections(category_slug, paper_id)
    if section:
        # Substring match, both directions: extraction names sections as they appear in the
        # source ("4 Why Self-Attention"), and a caller asks for "self-attention".
        wanted = section.lower()
        hit = next((name for name in sections
                    if wanted in name.lower() or name.lower() in wanted), "")
        if hit:
            lines.append(f"\n--- section: {hit} ---\n{sections[hit][:_MAX_SECTION_CHARS]}")
        else:
            lines.append(f"\n(no section matching '{section}'. This paper's sections are: "
                         f"{', '.join(sections) or 'none extracted'})")
    elif sections:
        # Named but not rendered, so a follow-up can ask for one by name instead of
        # concluding the paper has no body.
        lines.append(f"\nsections available (ask for one by name): {', '.join(sections)}")
    return "\n".join(lines)


def _render_idea(record: dict) -> str:
    lines = [f"IDEA {record.get('idea_id', '?')}"]
    for key in ("title", "one_liner", "problem", "description"):
        if value := record.get(key):
            lines.append(f"{key}: {value}")
    for key, value in (record.get("sections") or {}).items():
        rendered = "\n".join(f"- {v}" for v in value) if isinstance(value, list) else str(value)
        lines.append(f"{key}:\n{rendered}")
    if addresses := record.get("addresses"):
        lines.append(f"addresses: {', '.join(str(a) for a in addresses)}")
    return "\n".join(lines)


# --- lookup -------------------------------------------------------------------


def _load(category_slug: str, record_id: str, kind: Kind | None = None) -> dict | None:
    """One record by id, as `{kind, id, text}`. None when nothing on disk has that id."""
    kinds: tuple[Kind, ...] = (kind,) if kind else ("gap", "paper", "idea")
    for candidate in kinds:
        if candidate == "gap":
            _stage, raw = storage.find_gap(category_slug, record_id)
            if raw:
                return {"kind": "gap", "id": record_id, "text": _render_gap(raw)}
        elif candidate == "paper":
            raw = storage.read_paper_json(category_slug, record_id)
            if raw:
                return {"kind": "paper", "id": record_id, "raw": raw}
        elif candidate == "idea":
            raw = storage.read_idea_json(category_slug, record_id)
            if raw:
                return {"kind": "idea", "id": record_id, "text": _render_idea(raw)}
    return None


def resolve(category_slug: str, ids: list[str], section: str = "") -> list[dict]:
    """Records named by id, in the order asked for. Unknown ids are simply absent —
    the caller reports them, so "there is no such gap" is an answer rather than an error."""
    out: list[dict] = []
    for record_id in ids[:_MAX_RECORDS]:
        kind = next((k for prefix, k in _ID_PREFIXES.items()
                     if record_id.startswith(prefix)), None)
        if (found := _load(category_slug, record_id, kind)) is None:
            continue
        if found["kind"] == "paper":
            found = {"kind": "paper", "id": found["id"],
                     "text": _render_paper(category_slug, found["id"], found["raw"], section)}
        out.append(found)
    return out


def search(category_slug: str, query: str, section: str = "") -> list[dict]:
    """The best lexical matches for a query, across gaps, papers and ideas.

    Scored on overlapping terms, weighted toward titles: a title is what the record is
    *about*, while a description mentions many things in passing. Records scoring nothing
    are dropped rather than ranked last — a zero-overlap "best match" is noise the answer
    step would otherwise have to argue with.
    """
    wanted = _terms(query)
    if not wanted:
        return []

    scored: list[tuple[float, str, Kind]] = []

    for stage in storage.GAP_STAGES:
        for gap_id in storage.list_gap_ids(category_slug, stage):
            raw = storage.read_gap_json(category_slug, gap_id, stage) or {}
            title = _terms(f"{raw.get('title', '')} {raw.get('canonical_description', '')}")
            body = _terms(raw.get("description", ""))
            if score := 2.0 * len(wanted & title) + len(wanted & body):
                scored.append((score, gap_id, "gap"))

    for paper_id in storage.list_paper_ids(category_slug):
        raw = storage.read_paper_json(category_slug, paper_id) or {}
        metadata = raw.get("metadata") or {}
        title = _terms(str(metadata.get("title") or ""))
        body = _terms(str(metadata.get("abstract") or ""))
        keywords = _terms(" ".join(paper_access.get_keywords(category_slug, paper_id)))
        if score := 2.0 * len(wanted & title) + len(wanted & body) + 1.5 * len(wanted & keywords):
            scored.append((score, paper_id, "paper"))

    for idea_id in storage.list_idea_ids(category_slug):
        raw = storage.read_idea_json(category_slug, idea_id) or {}
        title = _terms(f"{raw.get('title', '')} {raw.get('one_liner', '')}")
        body = _terms(f"{raw.get('problem', '')} {raw.get('description', '')}")
        if score := 2.0 * len(wanted & title) + len(wanted & body):
            scored.append((score, idea_id, "idea"))

    scored.sort(key=lambda s: (-s[0], s[1]))
    out: list[dict] = []
    for _score, record_id, kind in scored[:_MAX_MATCHES]:
        if (found := _load(category_slug, record_id, kind)) is None:
            continue
        if found["kind"] == "paper":
            found = {"kind": "paper", "id": found["id"],
                     "text": _render_paper(category_slug, found["id"], found["raw"], section)}
        out.append(found)
        if len(out) >= _MAX_RECORDS:
            break
    return out


def lookup(category_slug: str, *, ids: list[str] | None = None, query: str = "",
           section: str = "") -> dict[str, Any]:
    """One workspace read: ids if given, otherwise a query. Returns a result dict shaped
    like the investigator's other routes — `records`, `text`, and `error` when empty."""
    ids = [i for i in (ids or []) if i]
    records = resolve(category_slug, ids, section) if ids else search(category_slug, query, section)

    found_ids = {r["id"] for r in records}
    unknown = [i for i in ids if i not in found_ids]

    result: dict[str, Any] = {
        "records": [{"kind": r["kind"], "id": r["id"]} for r in records],
        "unknown_ids": unknown,
        "text": "\n\n---\n\n".join(r["text"] for r in records),
    }
    if not records:
        result["error"] = (
            f"nothing in the '{category_slug}' workspace matches "
            + (f"the id(s) {', '.join(ids)}" if ids else f"'{query}'")
        )
    return result
