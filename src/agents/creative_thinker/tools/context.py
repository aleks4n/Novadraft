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
``ideas``, ``catalog``, ``findings``, ``user_input``, ``discussion``, ``documents``.

**Loaded records are rendered per type, not generically.** A generic renderer picks one
"body" field per record and drops the rest, which for a gap means losing the evidence
quote it rests on and for a paper means losing the whole structured summary — exactly the
material ideation runs on. Each type therefore has its own block, and only unrecognised
keys fall back to the generic one-line form.

The ``catalog`` key is different in kind from the rest and is rendered as such: it is an
index of what the workspace holds but did *not* load, one line per record, there so a run
knows what it could ask for. See :mod:`src.common.tools.catalog`.
"""

from __future__ import annotations

from typing import Any

from src.common.schemas.idea import IdeaRecord

_ID_KEYS = ("gap_id", "paper_id", "idea_id", "id")
_TITLE_KEYS = ("title", "name", "canonical_description")
_BODY_KEYS = ("description", "summary", "text", "content", "one_liner")

_MAX_ITEMS = 12
_MAX_BODY_CHARS = 1200
_MAX_EVIDENCE = 4  # quotes per gap; the rest repeat the same claim from other papers
_MAX_LIST = 6  # entries rendered from any one summary list
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


def _flat(item: object) -> str:
    """A summary list entry as one line. Records like ``{"name": ..., "task": ...}`` are
    flattened rather than shown as JSON; plain strings pass through."""
    if isinstance(item, dict):
        return " · ".join(str(v) for v in item.values() if v)
    return str(item)


def _bullets(label: str, values: object, pad: str = "  ") -> list[str]:
    items = [t for v in (values or []) if (t := _flat(v).strip())][:_MAX_LIST]
    if not items:
        return []
    return [f"{pad}{label}:"] + [f"{pad}  - {t}" for t in items]


def _gap_lines(gap: dict) -> list[str]:
    """One gap in full, including the sentences in the papers that it rests on.

    The evidence quotes are the reason this is not a one-liner. A gap's own text says
    *that* something is missing; the quote says which setting it was missing in, in the
    authors' words, and that is what an idea has to be built to work in. A run given the
    claim without the quote invents the setting.

    `verdict` and `ranking` are rendered as a status and a score and nothing more — the
    validator's reasoning is a paragraph about which prior work failed to close the gap,
    which reads as instructions to go check prior work rather than as material to think
    with.
    """
    lines = [f"### gap {gap.get('gap_id', '?')}"
             + (f"  [{gap['type']}]" if gap.get("type") else "")]
    if title := gap.get("title"):
        lines.append(f"**{title}**")
    if canonical := gap.get("canonical_description"):
        lines.append(f"canonical: {canonical}")
    if description := gap.get("description"):
        lines.append(str(description))

    evidence = gap.get("evidence") or []
    for item in evidence[:_MAX_EVIDENCE]:
        if not isinstance(item, dict):
            continue
        where = " / ".join(str(x) for x in (item.get("paper_id"), item.get("section")) if x)
        if quote := (item.get("quote") or item.get("anchor")):
            lines.append(f'- evidence ({where}): "{quote}"')
    if len(evidence) > _MAX_EVIDENCE:
        lines.append(f"- ({len(evidence) - _MAX_EVIDENCE} further evidence quote(s) not "
                     f"shown — ask the investigator for this gap by id to see them all)")

    verdict = gap.get("verdict") or {}
    if isinstance(verdict, dict) and verdict.get("status"):
        confidence = verdict.get("confidence")
        lines.append(f"- verdict: {verdict['status']}"
                     + (f" (confidence {confidence})" if confidence is not None else ""))
    ranking = gap.get("ranking") or {}
    if isinstance(ranking, dict) and (score := ranking.get("total")) is not None:
        lines.append(f"- ranked: {score}")
    return lines


def _paper_lines(paper: dict) -> list[str]:
    """One paper, from its structured summary — what it did, what it assumed, what it
    left open.

    This is the block the generic renderer used to lose. It picked `description` as the
    body and stopped, so the summary the paper agent built — problem, approach, results,
    stated limitations, open threads — never reached the one step whose whole job is to
    think with it. The full text is still deliberately absent: it is ~60k chars and the
    investigator fetches it on purpose when a run needs it.
    """
    pid = paper.get("paper_id", "?")
    year = paper.get("year")
    lines = [f"### paper {pid}" + (f" ({year})" if year else "")]
    if title := paper.get("title"):
        lines.append(f"**{title}**")

    summary = paper.get("summary")
    if not isinstance(summary, dict):
        # A session fetch, or a record written before summaries existed. The one-line
        # description is all there is.
        if description := paper.get("description"):
            lines.append(str(description)[:_MAX_BODY_CHARS])
        return lines

    if keywords := (summary.get("keywords") or paper.get("keywords")):
        lines.append(f"keywords: {', '.join(str(k) for k in keywords)}")

    problem = summary.get("problem") or {}
    if isinstance(problem, dict):
        if statement := problem.get("statement"):
            lines.append(f"problem: {statement}")
        if motivation := problem.get("motivation"):
            lines.append(f"motivation: {motivation}")
        lines.extend(_bullets("prior limitations", problem.get("prior_limitations")))

    approach = summary.get("approach") or {}
    if isinstance(approach, dict):
        if text := approach.get("summary"):
            lines.append(f"approach: {text}")
        lines.extend(_bullets("key ideas", approach.get("key_ideas")))
        # Assumptions are where ideas come from: an assumption named is a premise a run
        # can decide to drop, which is the cheapest source of a genuinely different idea.
        lines.extend(_bullets("assumptions", approach.get("assumptions")))

    results = summary.get("results") or {}
    if isinstance(results, dict):
        if headline := results.get("headline"):
            lines.append(f"results: {headline}")
        lines.extend(_bullets("caveats", results.get("caveats")))

    lines.extend(_bullets("stated limitations", summary.get("limitations_stated")))
    lines.extend(_bullets("open threads", summary.get("open_threads")))
    return lines


def _idea_lines(idea: dict) -> list[str]:
    """One related idea — enough to recognise and not duplicate it, not enough to work on.

    These are background: the idea under work arrives separately as the subject. What a
    run needs from them is "this ground is taken", so the spine is rendered and `runs`,
    `evaluation` and `provenance` are not.
    """
    lines = [f"### idea {idea.get('idea_id') or '(draft)'}"]
    if title := idea.get("title"):
        lines.append(f"**{title}**")
    for key in ("one_liner", "problem", "description"):
        if text := idea.get(key):
            lines.append(str(text)[:_MAX_BODY_CHARS])
    if addresses := idea.get("addresses"):
        lines.append(f"addresses: {', '.join(str(a) for a in addresses)}")
    return lines


def _user_input_lines(item: dict) -> list[str]:
    """A note or a dropped document, in full.

    In full because this is the only path these take into a run — nothing in the
    workspace holds a copy, so what is cut here is simply lost. A document nothing could
    extract says so in `text`, and `extracted` is what tells the two apart.
    """
    label = item.get("title") or item.get("id") or item.get("kind", "input")
    kind = "note" if item.get("kind") == "note" else "document"
    lines = [f"### {kind}: {label}"]
    if item.get("extracted") is False:
        lines.append("_(its text could not be read — you know it is here and no more)_")
    if text := item.get("text"):
        lines.append(str(text))
    return lines


def _catalog_lines(index: dict) -> list[str]:
    """The rest of the workspace, as an index of what exists.

    One line per record and no body, because this section answers "what could I ask
    for" and not "what does it say". A run that wants the record behind a line asks the
    investigator for it by id — which is a step the plan shows, rather than a silent
    reach into the workspace.

    The counts are stated outright. A run that has to infer how much exists from how many
    lines it can see will infer wrong, and a truncated list read as a complete one is how
    a run concludes that nothing on a topic exists when sixty entries were cut.
    """
    counts = index.get("counts") or {}
    omitted = index.get("omitted") or {}
    lines = ["_Not loaded — the rest of the workspace, by id. Ask the investigator for any "
             "of these by id if the task needs what is in it._"]
    for key, label in (("gaps", "gap"), ("papers", "paper"), ("ideas", "idea")):
        entries = index.get(key) or []
        if not entries:
            continue
        total = counts.get(key, len(entries))
        lines.append(f"\n**{label}s not in context ({total} total):**")
        for entry in entries:
            marker = " · ".join(str(x) for x in (entry.get("type"), entry.get("marker")) if x)
            title = entry.get("title") or "(untitled)"
            lines.append(f"- `{entry.get('id')}` {title}" + (f"  [{marker}]" if marker else ""))
        if dropped := omitted.get(key, 0):
            lines.append(f"- (…and {dropped} more {label}(s), not listed here. Ask the "
                         f"investigator to search if what you need may be among them.)")
    return lines


def _generic_lines(items: list[dict]) -> list[str]:
    """The old one-line-per-record form, kept for context keys with no block of their own
    — `discussion` and `documents` today, and whatever a plan loads next."""
    lines = []
    for item in items:
        ident = _pick(item, _ID_KEYS)
        title = _pick(item, _TITLE_KEYS)
        body = _pick(item, _BODY_KEYS)[:_MAX_BODY_CHARS]
        head = " — ".join(p for p in (ident, title) if p)
        lines.append(f"- **{head or 'item'}**: {body}" if body else f"- **{head}**")
    return lines


# Keys with a renderer of their own. Order is the order they appear in the prompt: what
# the run is about first, what it may reach for last.
_BLOCKS = {
    "gaps": _gap_lines,
    "papers": _paper_lines,
    "ideas": _idea_lines,
    "user_input": _user_input_lines,
}
_HEADINGS = {
    "gaps": "Gaps in scope",
    "papers": "Papers behind those gaps",
    "ideas": "Related ideas that already exist",
    "user_input": "What the user put into this session",
    "findings": "Findings from earlier steps this turn",
    "catalog": "Also in the workspace (index only)",
    "missing_ids": "Could not be loaded",
}
_ORDER = ("gaps", "papers", "ideas", "user_input", "findings", "catalog", "missing_ids")


def render_context(context: dict[str, Any]) -> str:
    """The loaded context as markdown, per record type, capped so one huge PDF cannot
    crowd out the gap the run is actually about.

    Ordering is fixed by :data:`_ORDER` rather than taken from dict insertion, so the
    prompt reads the same way every run regardless of how the context was built, and the
    index of what was *not* loaded sits at the end where it cannot be mistaken for
    material.
    """
    context = context or {}
    keys = [k for k in _ORDER if k in context]
    keys += [k for k in context if k not in _HEADINGS]  # unrecognised, generic fallback

    blocks: list[str] = []
    for key in keys:
        value = context.get(key)
        heading = _HEADINGS.get(key, key)

        if key == "catalog":
            # Checked inline rather than through `catalog.is_empty`: that module reads the
            # filesystem, and the thinker must not be able to import its way to the
            # workspace. It renders an index it was handed; it never builds one.
            if not isinstance(value, dict) or not any(
                    value.get(k) for k in ("gaps", "papers", "ideas")):
                continue
            blocks.append("\n".join([f"## {heading}"] + _catalog_lines(value)))
            continue

        if key == "missing_ids":
            if items := [str(v) for v in (value or [])]:
                blocks.append(f"## {heading}\n" + ", ".join(items))
            continue

        if key == "findings":
            if items := [str(v) for v in (value or []) if v]:
                blocks.append(f"## {heading}\n" + "\n\n".join(items))
            continue

        items = _as_items(value)[:_MAX_ITEMS]
        if not items:
            continue
        lines = [f"## {heading}"]
        if block := _BLOCKS.get(key):
            for item in items:
                lines.extend(block(item))
                lines.append("")
        else:
            lines.extend(_generic_lines(items))
        blocks.append("\n".join(lines).rstrip())
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
