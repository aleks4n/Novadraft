"""Map persistent workspace JSON → the Research Ideation v2 design data model.

Pure functions, no I/O. The design (ported from the claude.ai "Research Ideation
v2" project) expects ``topic → { meta, papers[], gaps[], ideas[] }``; the backend
stores ``workspace/<slug>/{meta.json, papers/<id>/paper.json, gaps/pending/*.json}``
shaped by ``src/common/schemas/{summary,gap}.py``. These functions bridge the two.

Fields the current agents don't produce (relevance / novelty / feasibility
scores, repo, collaborators, goals, citations) are returned as ``None``/empty so
the UI can hide the corresponding widget rather than invent a value.
"""
from __future__ import annotations

# Severity shown on gap cards is derived from the stored gap ``type`` — the gap
# agent classifies the kind of gap but doesn't score it.
_SEVERITY_BY_TYPE = {
    "limitation": "high",
    "negative_result": "high",
    "threat_to_validity": "high",
    "future_work": "medium",
    "open_problem": "medium",
    "caveat": "low",
    "assumption": "low",
}

# Stable, warm accent per topic — keyed by a hash of the slug so a workspace
# keeps its colour across reloads. Palette lifted from the design tokens.
_TOPIC_COLORS = ["#5B5BF6", "#5B9C75", "#D9A04C", "#C75B5B", "#5B7BC7", "#8B5CF6"]


def topic_color(slug: str) -> str:
    return _TOPIC_COLORS[sum(map(ord, slug)) % len(_TOPIC_COLORS)]


def _short_title(title: str | None, fallback: str, limit: int = 34) -> str:
    if not title:
        return fallback
    title = title.strip()
    return title if len(title) <= limit else title[: limit - 1].rstrip() + "…"


def _authors_str(authors: list[str] | None) -> str:
    authors = authors or []
    if not authors:
        return "Unknown authors"
    if len(authors) <= 3:
        return ", ".join(authors)
    return ", ".join(authors[:3]) + ", et al."


# The paper agent's persisted lifecycle: one boolean flag per stage in
# ``paper.json``'s ``processing`` block. Order = pipeline order. Surfaced as a
# stepper so the UI shows *where* a paper got to (and where it stalled), not just
# a single collapsed label.
_PAPER_STAGES = [
    ("fetched", "Fetched"),
    ("extracted", "Extracted"),
    ("summarized", "Summarized"),
    ("summary_verified", "Verified"),
    ("gaps_extracted", "Gaps"),
]


def _paper_stages(processing: dict) -> list[dict]:
    """Per-stage done flags, in pipeline order (drives the row/detail stepper)."""
    p = processing or {}
    return [{"key": k, "label": label, "done": bool(p.get(k))} for k, label in _PAPER_STAGES]


def _paper_status(processing: dict) -> str:
    """One-word lifecycle label for the paper, most-advanced stage first."""
    p = processing or {}
    if p.get("gaps_extracted"):
        return "complete"
    if p.get("summary_verified"):
        return "verified"
    if p.get("summarized"):
        return "summarized"
    if p.get("extracted"):
        return "extracted"
    if p.get("fetched"):
        return "fetched"
    return "pending"


def _pipeline_complete(processing: dict) -> bool:
    """True once every pipeline stage flag is set — used to offer a resume."""
    p = processing or {}
    return all(p.get(k) for k, _ in _PAPER_STAGES)


def _needs_source(processing: dict, source: dict) -> bool:
    """True when the paper is fetched but has no usable full-text source yet
    (no pdf, no main .tex) and hasn't been extracted — the state the user
    resolves by dropping in a pdf/tex source."""
    p = processing or {}
    if not p.get("fetched") or p.get("extracted"):
        return False
    files = ((source or {}).get("files")) or {}
    return not (files.get("pdf") or files.get("tex_main"))


def _map_verification(rec: dict) -> dict | None:
    """The summarize agent's self-check — its grounding/critique result.

    This is the part of the agent process worth surfacing on the paper itself:
    did the summary verify clean, how many regeneration attempts it took, and
    which claims it could not ground back to the source text.
    """
    v = rec.get("summary_verification") or {}
    if not v:
        return None
    grounding = v.get("grounding") or {}
    issues = v.get("critique_issues") or []
    return {
        "status": v.get("status"),  # "ok" | "issues"
        "attempts": v.get("attempts"),
        "regenerated": bool(v.get("regenerated")),
        "groundedClaims": len(grounding.get("checked") or []),
        "unmatchedClaims": list(grounding.get("unmatched") or []),
        "issueCount": len(issues),
    }


# --- paper -------------------------------------------------------------------

def map_paper(rec: dict, *, full: bool = False) -> dict:
    """A stored ``paper.json`` record → the design's paper object.

    ``full`` includes the heavier survey/findings detail used by PaperDetail;
    list views pass ``full=False`` for a lighter row.
    """
    meta = rec.get("metadata") or {}
    summary = rec.get("summary") or {}
    processing = rec.get("processing") or {}
    source = rec.get("source") or {}
    pid = rec.get("paper_id", "")

    venue = meta.get("venue")
    if not venue:
        venue = "arXiv" if (meta.get("identifiers") or {}).get("arxiv_id") else "—"

    out = {
        "id": pid,
        "title": meta.get("title") or pid,
        "short": _short_title(meta.get("title"), pid),
        "authors": _authors_str(meta.get("authors")),
        "venue": venue,
        "year": meta.get("year"),
        "citations": None,  # not collected by the fetch agent
        "tags": (summary.get("topics") or [])[:6],
        "status": _paper_status(processing),
        "stages": _paper_stages(processing),
        "complete": _pipeline_complete(processing),
        # Resolved (metadata fetched) but no downloadable full text yet, and not
        # past extraction — the UI offers a drag-and-drop to supply a pdf/tex
        # source so the stalled pipeline can resume. Mirrors fetch_node's
        # ``needs_source`` short-circuit (src/agents/paper_agent/agent.py).
        "needsSource": _needs_source(processing, source),
        # The original query the paper was fetched from — re-running the agent
        # with it resumes the pipeline from the first unfinished stage.
        "resumeQuery": source.get("input_query") or pid,
        "gapCount": len((rec.get("gaps") or {}).get("gap_ids") or []),
        "warnings": list(rec.get("warnings") or []),
        "addedAt": (source.get("fetched_at") or "")[:10],
        "addedBy": "—",
        "repo": None,
        "hasSummary": bool(summary),
    }
    if not full:
        return out

    out["abstract"] = meta.get("abstract") or ""
    out["keyFindings"] = [
        c.get("description", "") for c in (summary.get("contributions") or [])
    ]
    out["identifiers"] = meta.get("identifiers") or {}
    out["sourceUrl"] = source.get("source_url")
    out["verification"] = _map_verification(rec)

    if summary:
        problem = summary.get("problem") or {}
        approach = summary.get("approach") or {}
        results = summary.get("results") or {}
        weaknesses = list(summary.get("limitations_stated") or [])
        weaknesses += list(summary.get("open_threads") or [])
        out["survey"] = {
            "summary": summary.get("context_summary") or approach.get("summary") or "",
            "strengths": approach.get("key_ideas") or [],
            "weaknesses": weaknesses,
            "relevance": None,  # no relevance score is produced yet
            "paperType": summary.get("paper_type"),
            "keywords": summary.get("keywords") or [],
            # Structured detail pulled straight from the summarize agent output.
            "problem": problem.get("statement") or "",
            "motivation": problem.get("motivation") or "",
            "priorLimitations": list(problem.get("prior_limitations") or []),
            "resultsHeadline": results.get("headline") or "",
            "resultsCaveats": list(results.get("caveats") or []),
            "datasets": [
                d.get("name", "") for d in (summary.get("datasets") or []) if d.get("name")
            ],
            "metrics": [
                {
                    "name": m.get("name", ""),
                    "value": m.get("value", ""),
                    "dataset": m.get("dataset", ""),
                    "comparison": m.get("comparison", ""),
                }
                for m in (summary.get("metrics") or [])
            ],
        }
    else:
        out["survey"] = None
    return out


# --- gap ---------------------------------------------------------------------

def map_gap(rec: dict) -> dict:
    """A stored ``gaps/pending/<id>.json`` GapRecord → the design's gap object."""
    evidence = rec.get("evidence") or []
    derived_from = list(dict.fromkeys(e.get("paper_id") for e in evidence if e.get("paper_id")))
    gtype = rec.get("type", "open_problem")
    return {
        "id": rec.get("gap_id", ""),
        "title": rec.get("title", ""),
        "short": _short_title(rec.get("title"), rec.get("gap_id", "")),
        "description": rec.get("description", ""),
        "type": gtype,
        "severity": _SEVERITY_BY_TYPE.get(gtype, "medium"),
        "canonicalDescription": rec.get("canonical_description", ""),
        "derivedFrom": derived_from,
        "evidence": [
            {
                "paperId": e.get("paper_id"),
                "section": e.get("section"),
                "quote": e.get("quote"),
                # The gap agent records ``char_offset`` [start, end] when it locates
                # the quote verbatim in the source section; its presence is the
                # grounding signal (an ungrounded quote carries no offset).
                "charOffset": e.get("char_offset"),
                "grounding": e.get("grounding_status")
                or ("matched" if e.get("char_offset") else "not_found"),
            }
            for e in evidence
        ],
        "novelty": None,       # not scored by the gap agent yet
        "feasibility": None,
        "questions": [],
        "relatedIdeas": [],
        "status": rec.get("status", "unchecked"),
        "extractedAt": (rec.get("extracted_at") or "")[:10],
    }


# --- idea ---------------------------------------------------------------------

def map_idea(rec: dict) -> dict:
    """A stored ``ideas/<id>.json`` IdeaRecord → the design's idea object."""
    ev = rec.get("evaluation") or {}
    return {
        "id": rec.get("idea_id", ""),
        "title": rec.get("title", ""),
        "short": rec.get("short") or _short_title(rec.get("title"), rec.get("idea_id", "")),
        "description": rec.get("description", ""),
        "summary": rec.get("summary", ""),
        "status": rec.get("status", "draft"),
        "source": rec.get("source", "agent"),
        "novelty": ev.get("novelty_score"),
        "feasibility": ev.get("feasibility_score"),
        "impact": ev.get("impact_score"),
        "relatedGaps": rec.get("related_gaps", []),
        "relatedPapers": rec.get("related_papers", []),
        "method": rec.get("method", []),
        "evaluation": {
            "noveltyScore": ev.get("novelty_score"),
            "novelty": ev.get("novelty", ""),
            "feasibilityScore": ev.get("feasibility_score"),
            "feasibility": ev.get("feasibility", ""),
            "impactScore": ev.get("impact_score"),
            "impact": ev.get("impact", ""),
            "rigourScore": ev.get("rigour_score"),
            "rigour": ev.get("rigour", ""),
            "risks": ev.get("risks", []),
        },
        "createdAt": (rec.get("created_at") or "")[:10],
        "updatedAt": (rec.get("updated_at") or "")[:10],
    }


# --- topic / meta ------------------------------------------------------------

def map_topic(meta: dict, *, n_papers: int, n_gaps: int, n_ideas: int = 0) -> dict:
    """A workspace ``meta.json`` (+ counts) → the design's topic ``meta`` object."""
    slug = meta.get("category_slug", "")
    name = meta.get("name") or slug
    return {
        "id": slug,
        "slug": slug,
        "title": name,
        "shortTitle": name,
        "category": meta.get("category") or "Research workspace",
        "categoryColor": topic_color(slug),
        "description": meta.get("description") or "",
        "createdAt": (meta.get("created_at") or "")[:10],
        "updatedAt": (meta.get("updated_at") or "")[:10],
        "starred": bool(meta.get("starred")),
        "goals": meta.get("goals") or [],
        "keywords": meta.get("keywords") or [],
        "collaborators": meta.get("collaborators") or [],
        "stats": {
            "papers": n_papers,
            "gaps": n_gaps,
            "ideas": n_ideas,
            "sessionsThisWeek": meta.get("sessions_this_week") or 0,
        },
    }
