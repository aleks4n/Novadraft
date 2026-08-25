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

# A gap flows down the pipeline through stage folders; each stage is one step of
# the ingestion lifecycle the paper page surfaces per gap:
#   pending      → mined by the gap agent, awaiting the director
#   to_validate  → the director judged it novel, awaiting the validator
#   to_rank      → the validator judged it an open opportunity, awaiting ranking
#   master       → in the master list (a ranked, live opportunity)
#   archive      → retired: a director duplicate (merged into another gap) or a
#                  validator-solved gap (already addressed by prior art)
_GAP_STEPS = [
    ("mined", "Mined"),
    ("deduped", "Deduplicated"),
    ("validated", "Validated"),
    ("ranked", "Ranked"),
]


def _map_verdict(v: dict | None) -> dict | None:
    """The validator's judgment on a gap: whether prior art has since addressed
    it, and which papers do so."""
    if not v:
        return None
    return {
        "status": v.get("status"),  # "open" | "partially_solved" | "solved"
        "confidence": v.get("confidence"),
        "reasoning": v.get("reasoning", ""),
        "addressedBy": [
            {
                "paperId": (a.get("paper") or {}).get("paper_id"),
                "title": (a.get("paper") or {}).get("title"),
                "url": (a.get("paper") or {}).get("url"),
                "relevance": a.get("relevance_to_gap"),
                "contribution": a.get("contribution_summary", ""),
                "remainsOpen": a.get("what_remains_open", ""),
            }
            for a in (v.get("assessments") or [])
        ],
    }


def _map_ranking(r: dict | None) -> dict | None:
    """The ranking agent's three dimension scores for an open opportunity."""
    if not r:
        return None
    return {
        "openness": r.get("openness"),
        "impact": r.get("impact"),
        "feasibility": r.get("feasibility"),
        "feasibilityReasoning": r.get("feasibility_reasoning", ""),
    }


def map_ranking_config(cfg) -> dict:
    """The RankingConfig's *view-time* knobs, for the UI's ranking panel.

    Only the knobs the UI actually applies at view time are exposed: the three
    dimension weights (`w_*`, used in the weighted geometric mean that produces
    the Overall score) and the three filter floors (`min_*`, below which a gap is
    dropped). The compute-time knobs (venue/citation/recency) are the agent's and
    are not surfaced here. `cfg` is a RankingConfig (or any object with these
    attributes)."""
    return {
        "wOpenness": cfg.w_openness,
        "wImpact": cfg.w_impact,
        "wFeasibility": cfg.w_feasibility,
        "minOpenness": cfg.min_openness,
        "minImpact": cfg.min_impact,
        "minFeasibility": cfg.min_feasibility,
    }


def _gap_ingestion(rec: dict, stage: str | None) -> dict:
    """Where the gap is in the ingestion pipeline + a per-step stepper.

    ``outcome`` is the branch a gap took at a stage — ``duplicate`` (director),
    or the validator's ``open`` / ``partially_solved`` / ``solved`` — so the UI can
    badge a gap even though its physical ``stage`` folder only says *where* it sits.
    """
    verdict = rec.get("verdict") or {}
    ranking = rec.get("ranking")
    # The archive/ stage holds two kinds of retired gap: director duplicates and
    # validator-solved gaps. Detect a duplicate from its own markers, not the
    # stage folder, so a solved gap parked in archive/ keeps its "solved" outcome.
    is_dup = rec.get("status") == "duplicate" or bool(rec.get("duplicate_of"))

    deduped = stage in ("to_validate", "to_rank", "master", "archive")
    validated = bool(verdict) or stage in ("to_rank", "master")
    ranked = ranking is not None

    if is_dup:
        outcome = "duplicate"
    elif verdict:
        outcome = verdict.get("status")  # open | partially_solved | solved
    elif stage in ("to_validate",):
        outcome = "novel"
    else:
        outcome = None

    def _state(key: str, done: bool) -> str:
        # Duplicates are merged after dedup, so validate/rank never apply to them.
        if is_dup and key in ("validated", "ranked"):
            return "skipped"
        # Solved gaps are terminal (parked in archive), never ranked.
        if key == "ranked" and verdict.get("status") == "solved":
            return "skipped"
        return "done" if done else "todo"

    done_by_key = {"mined": True, "deduped": deduped,
                   "validated": validated, "ranked": ranked}
    steps = [
        {"key": k, "label": label, "done": done_by_key[k], "state": _state(k, done_by_key[k])}
        for k, label in _GAP_STEPS
    ]
    return {
        "stage": stage,
        "outcome": outcome,
        "duplicateOf": rec.get("duplicate_of"),
        "steps": steps,
    }


def map_gap(rec: dict, stage: str | None = None) -> dict:
    """A stored ``gaps/<stage>/<id>.json`` GapRecord → the design's gap object.

    ``stage`` is the folder the record was read from — it drives the per-gap
    ingestion stepper and lets the UI badge duplicates / validated / ranked gaps.
    """
    evidence = rec.get("evidence") or []
    derived_from = list(dict.fromkeys(e.get("paper_id") for e in evidence if e.get("paper_id")))
    gtype = rec.get("type", "open_problem")
    verdict = _map_verdict(rec.get("verdict"))
    ranking = _map_ranking(rec.get("ranking"))
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
        # ingestion pipeline surface (director / validator / ranking outputs)
        "stage": stage,
        "ingestion": _gap_ingestion(rec, stage),
        "duplicateOf": rec.get("duplicate_of"),
        "verdict": verdict,
        "ranking": ranking,
    }


# --- idea ---------------------------------------------------------------------

SECTION_LABELS = {
    "technique": "Technique",
    "rationale": "Rationale",
    "open_questions": "Open questions",
    "method": "Method",
    "evaluation_plan": "Evaluation plan",
    "datasets": "Datasets",
    "risks": "Risks",
    "related_work": "Related work",
}


def _section_label(key: str) -> str:
    return SECTION_LABELS.get(key) or key.replace("_", " ").strip().capitalize()


def _section_kind(value) -> str:
    """How the section renders and edits: a list of lines, or a markdown block."""
    return "list" if isinstance(value, (list, tuple)) else "markdown"


def map_sections(sections: dict | None, order: list | None = None) -> list[dict]:
    """The open part of an idea, as an *ordered list* rather than a map.

    ``sections`` is deliberately unvalidated (see ``src/common/schemas/idea.py``),
    so the UI needs a stable render contract instead: each entry carries its slug,
    a display label, the value, and whether it is a list or markdown.

    ``section_order`` decides the reading order, with anything it does not name
    falling in after, in map order — so a section added without touching the list
    is never hidden. Sorting here is never the UI's own idea: the order is the
    document's structure, and only a ``reorder`` edit changes it.
    """
    sections = sections or {}
    listed = [k for k in (order or []) if k in sections]
    keys = listed + [k for k in sections if k not in set(listed)]
    out = []
    for key in keys:
        value = sections[key]
        kind = _section_kind(value)
        out.append({
            "key": key,
            "label": _section_label(key),
            "kind": kind,
            "value": [str(v) for v in value] if kind == "list" else str(value or ""),
        })
    return out


def _map_run_entry(run: dict) -> dict:
    """One ``runs[]`` entry → what the thinking-graph surface renders.

    Nodes and edges come through whole: the decision-graph window draws the tree,
    colours nodes by status and badges them by style, and none of that can be
    reconstructed from a summary.
    """
    header = run.get("header") or {}
    outcome = run.get("outcome") or {}
    # Entries written before RunSummary existed stored one free line here; the
    # schema reads that as the objective (``RunSummary._accept_legacy_string``)
    # and so must this, or one old run takes the whole idea page down.
    summary = outcome.get("summary") or {}
    if isinstance(summary, str):
        summary = {"objective": summary}
    elif not isinstance(summary, dict):
        summary = {}
    return {
        "runId": run.get("run_id", ""),
        "sessionId": run.get("session_id"),
        "timestamp": run.get("timestamp", ""),
        "mode": header.get("mode", ""),
        "width": header.get("width"),
        "depth": header.get("depth"),
        "model": header.get("model", ""),
        "styles": header.get("agent_styles") or [],
        "task": header.get("task", ""),
        "triggeringAction": header.get("triggering_action", ""),
        "contextIds": header.get("context_ids") or [],
        "nodes": [
            {
                "id": n.get("node_id", ""),
                "parentId": n.get("parent_id"),
                "text": n.get("text", ""),
                "title": n.get("title", ""),
                "style": n.get("style", "neutral"),
                "score": n.get("score"),
                "status": n.get("status", "kept"),
                "kind": n.get("kind", "branch"),
                "depth": n.get("depth", 0),
                "comparison": n.get("comparison", ""),
                "question": n.get("question"),
            }
            for n in (run.get("nodes") or [])
        ],
        "edges": [
            {"from": e.get("from") or e.get("from_node"),
             "to": e.get("to") or e.get("to_node"),
             "type": e.get("type", "expanded-into")}
            for e in (run.get("edges") or [])
        ],
        "outcome": {
            "status": outcome.get("status", "completed"),
            "reason": outcome.get("reason", ""),
            "limit": outcome.get("limit"),
            "acceptedNode": outcome.get("accepted_node"),
            "acceptedNodes": outcome.get("accepted_nodes") or [],
            "escalation": outcome.get("escalation"),
            "escalationNode": outcome.get("escalation_node"),
            "escalationDiagnosis": outcome.get("escalation_diagnosis", ""),
            "answer": outcome.get("answer", ""),
            # Changes the run recommended but did not make. The user turns one into
            # the next run's task, so it has to be visible next to the answer.
            "proposedChanges": outcome.get("proposed_changes") or [],
            "truncated": bool(outcome.get("truncated")),
            "summary": {
                "objective": summary.get("objective", ""),
                "accepted": summary.get("accepted", ""),
                "rejected": summary.get("rejected", ""),
                "open": summary.get("open") or [],
            },
        },
    }


def map_idea(rec: dict, *, full: bool = True) -> dict:
    """A stored ``ideas/<id>.json`` :class:`IdeaRecord` → the design's idea object.

    Follows the record's four parts: the validated **spine** (title, one-liner,
    description, problem, addresses, relations), the open **sections**, the
    multi-writer **evaluation/provenance** blocks, and the append-only **runs**
    graph. ``full=False`` omits ``runs`` — the list and graph views only need the
    spine, and one idea's trace can be larger than every other field combined.

    A handful of Phase-1 aliases (``relatedGaps``, ``novelty``…) are kept so the
    ideas list and cards keep working; each reads the real field underneath.
    """
    ev = rec.get("evaluation") or {}
    origin = rec.get("origin") or {}
    sections = map_sections(rec.get("sections"), rec.get("section_order"))
    # Spine-level per the design of record; records written before it lived there
    # carry the same two values inside ``origin``.
    inherited = rec.get("inherited_from")
    if not isinstance(inherited, dict):
        inherited = ({"idea_id": origin.get("inherited_from"),
                      "upto_run_id": origin.get("inherited_upto_run")}
                     if origin.get("inherited_from") else None)
    runs = rec.get("runs") or []
    idea_id = rec.get("idea_id") or ""
    # The three axes the record actually stores (novelty / feasibility /
    # significance). ``impact`` is the design's name for significance; a legacy
    # ``*_score`` key still wins where a Phase-1 record has one.
    novelty = ev.get("novelty", ev.get("novelty_score"))
    feasibility = ev.get("feasibility", ev.get("feasibility_score"))
    impact = ev.get("significance", ev.get("impact_score", ev.get("impact")))
    return {
        "id": idea_id,
        "title": rec.get("title", ""),
        "short": _short_title(rec.get("title"), idea_id),
        "oneLiner": rec.get("one_liner", ""),
        "description": rec.get("description", ""),
        "problem": rec.get("problem", ""),
        "state": rec.get("state", "provisional"),
        "addresses": rec.get("addresses") or [],
        "relations": [
            {
                "type": r.get("type", ""),
                "target": r.get("target", ""),
                "rationale": r.get("rationale", ""),
                "run": r.get("run"),
            }
            for r in (rec.get("relations") or [])
            if isinstance(r, dict) and r.get("target")
        ],
        "sections": sections,
        "sectionOrder": [s["key"] for s in sections],
        "inheritedFrom": {
            "ideaId": inherited.get("idea_id"),
            "uptoRunId": inherited.get("upto_run_id"),
        } if inherited else None,
        "provenance": [
            {"claim": p.get("claim", ""), "source": p.get("source", ""), "via": p.get("via", "")}
            for p in (rec.get("provenance") or []) if isinstance(p, dict)
        ],
        "origin": {
            "runId": origin.get("run_id"),
            "nodeId": origin.get("node_id"),
            "style": origin.get("style", ""),
            "mode": origin.get("mode", ""),
            "parentIdeaId": origin.get("parent_idea_id"),
            "suggested": origin.get("suggested"),
            "inheritedFrom": origin.get("inherited_from"),
        } if origin else None,
        "runCount": len(runs),
        "runs": [_map_run_entry(r) for r in runs] if full else [],
        "novelty": novelty,
        "feasibility": feasibility,
        "impact": impact,
        "evaluation": {
            "noveltyScore": novelty,
            "novelty": ev.get("novelty_reasoning", "") if isinstance(ev.get("novelty_reasoning"), str) else "",
            "feasibilityScore": feasibility,
            "feasibility": ev.get("feasibility_reasoning", "") if isinstance(ev.get("feasibility_reasoning"), str) else "",
            "impactScore": impact,
            "impact": ev.get("significance_reasoning", "") if isinstance(ev.get("significance_reasoning"), str) else "",
            "rigourScore": ev.get("rigour_score"),
            "rigour": ev.get("rigour", "") if isinstance(ev.get("rigour"), str) else "",
            "risks": ev.get("risks") or [],
        },
        # Phase-1 aliases — the ideas list/cards still read these names.
        "summary": rec.get("one_liner", ""),
        "status": "draft" if rec.get("state") == "provisional" else "active",
        "source": "user" if (origin.get("mode") or "") == "user" else "agent",
        "relatedGaps": rec.get("addresses") or [],
        "relatedPapers": [p.get("source", "") for p in (rec.get("provenance") or [])
                          if isinstance(p, dict) and str(p.get("source", "")).startswith(("arxiv_", "doi_", "paper_"))],
        "method": next((s["value"] for s in sections
                        if s["key"] in ("method", "technique") and s["kind"] == "list"), []),
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
        # Foundational papers the category agent wrote into meta.json. Each is a
        # one-click ingest into the paper pipeline (the frontend passes ``title``
        # as the fetch query), so the field carries just what a paper card needs.
        "seminalPapers": [
            {
                "title": p.get("title", ""),
                "authors": p.get("authors", ""),
                "year": p.get("year"),
                "why": p.get("why", ""),
            }
            for p in (meta.get("seminal_papers") or [])
            if isinstance(p, dict) and p.get("title")
        ],
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
