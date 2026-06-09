"""PaperFetchAgent entry point.

Returns one of:
  - {"status": "ok",        "paper": <paper.json dict>}
  - {"status": "ambiguous", "candidates": [...], "message": "..."}

Raises:
  - PaperFetchError          — nothing found
  - PaperAlreadyExistsError  — paper already in the workspace
"""
from __future__ import annotations

from . import storage
from .classify import classify
from .errors import PaperAlreadyExistsError, PaperFetchError
from .fetchers.arxiv import fetch_arxiv
from .fetchers.doi import fetch_doi
from .fetchers.landing import resolve_landing
from .fetchers.pdf_url import fetch_pdf_url
from .fetchers.title import resolve_title


def _ok(paper: dict) -> dict:
    return {"status": "ok", "paper": paper}


def _ambiguous(candidates: list[dict], query: str) -> dict:
    return {
        "status": "ambiguous",
        "message": (
            f"No exact match for {query!r}. Pick one by re-running with its arXiv ID or DOI."
        ),
        "candidates": [
            {
                "title": c.get("title"),
                "authors": c.get("authors") or [],
                "year": c.get("year"),
                "arxiv_id": c.get("arxiv_id"),
                "doi": c.get("doi"),
                "source": c.get("source"),
            }
            for c in candidates
        ],
    }


def _check_usable(record: dict, query: str, source: str) -> dict:
    has_pdf = bool(record["source"]["files"].get("pdf"))
    md = record["metadata"]
    has_meta = bool(md.get("title")) and any(md["identifiers"].values())
    if has_pdf or has_meta:
        return record
    reason = "; ".join(record.get("warnings") or []) or "no metadata or pdf"
    raise PaperFetchError(query, [(source, reason)])


def _guard_existing(category_slug: str, paper_id: str, query: str, input_type: str):
    if storage.paper_exists(category_slug, paper_id):
        storage.log_fetch(category_slug, query, input_type, "duplicate",
                         paper_id=paper_id, error=f"already exists")
        raise PaperAlreadyExistsError(category_slug, paper_id)


def _do_arxiv(arxiv_id, category_slug, input_type, query, source):
    paper_id = storage.arxiv_paper_id(arxiv_id)
    _guard_existing(category_slug, paper_id, query, input_type)
    rec = fetch_arxiv(arxiv_id, category_slug, input_type, query)
    _check_usable(rec, query, source)
    storage.log_fetch(category_slug, query, input_type, "success",
                     paper_id=paper_id, extra={
                         "source": source,
                         "title": rec["metadata"].get("title"),
                         "authors": rec["metadata"].get("authors", [])[:3],
                         "year": rec["metadata"].get("year"),
                         "arxiv_id": rec["metadata"]["identifiers"].get("arxiv_id"),
                         "doi": rec["metadata"]["identifiers"].get("doi"),
                     })
    return rec


def _do_doi(doi, category_slug, input_type, query, source):
    paper_id = storage.doi_paper_id(doi)
    _guard_existing(category_slug, paper_id, query, input_type)
    rec = fetch_doi(doi, category_slug, input_type, query)
    _check_usable(rec, query, source)
    storage.log_fetch(category_slug, query, input_type, "success",
                     paper_id=paper_id, extra={
                         "source": source,
                         "title": rec["metadata"].get("title"),
                         "authors": rec["metadata"].get("authors", [])[:3],
                         "year": rec["metadata"].get("year"),
                         "arxiv_id": rec["metadata"]["identifiers"].get("arxiv_id"),
                         "doi": rec["metadata"]["identifiers"].get("doi"),
                     })
    return rec


def _do_pdf(url, category_slug, input_type, query, source):
    paper_id = storage.title_paper_id(url)
    _guard_existing(category_slug, paper_id, query, input_type)
    rec = fetch_pdf_url(url, category_slug, input_type, query)
    _check_usable(rec, query, source)
    storage.log_fetch(category_slug, query, input_type, "success",
                     paper_id=paper_id, extra={
                         "source": source,
                         "title": rec["metadata"].get("title"),
                         "authors": rec["metadata"].get("authors", [])[:3],
                         "year": rec["metadata"].get("year"),
                         "arxiv_id": rec["metadata"]["identifiers"].get("arxiv_id"),
                         "doi": rec["metadata"]["identifiers"].get("doi"),
                     })
    return rec


def fetch(category_slug: str, query: str) -> dict:
    storage.ensure_category(category_slug)
    c = classify(query)

    try:
        if c.input_type in ("arxiv_id", "arxiv_url"):
            return _ok(_do_arxiv(c.value, category_slug, c.input_type, query, "arxiv"))

        if c.input_type in ("doi", "doi_url"):
            return _ok(_do_doi(c.value, category_slug, c.input_type, query, "doi"))

        if c.input_type == "pdf_url":
            return _ok(_do_pdf(c.value, category_slug, c.input_type, query, "pdf_url"))

        if c.input_type in ("landing_url", "url"):
            resolved = resolve_landing(c.value)
            if "arxiv_id" in resolved:
                return _ok(_do_arxiv(resolved["arxiv_id"], category_slug, c.input_type, query, "landing->arxiv"))
            if "doi" in resolved:
                return _ok(_do_doi(resolved["doi"], category_slug, c.input_type, query, "landing->doi"))
            if "pdf_url" in resolved:
                return _ok(_do_pdf(resolved["pdf_url"], category_slug, c.input_type, query, "landing->pdf"))
            return _ok(_do_pdf(c.value, category_slug, c.input_type, query, "url_as_pdf"))

        # Title path: search → match-or-disambiguate
        chosen, candidates = resolve_title(c.value, query)
        if chosen is None:
            storage.log_fetch(category_slug, query, c.input_type, "ambiguous",
                             extra={
                                 "num_candidates": len(candidates),
                                 "candidates": [
                                     {
                                         "title": c.get("title"),
                                         "authors": c.get("authors", [])[:2],
                                         "year": c.get("year"),
                                         "arxiv_id": c.get("arxiv_id"),
                                         "doi": c.get("doi"),
                                         "source": c.get("source"),
                                     }
                                     for c in candidates
                                 ],
                             })
            return _ambiguous(candidates, query)

        if chosen.get("arxiv_id"):
            return _ok(_do_arxiv(chosen["arxiv_id"], category_slug, c.input_type, query,
                                 f"title->arxiv ({chosen['source']})"))
        if chosen.get("doi"):
            return _ok(_do_doi(chosen["doi"], category_slug, c.input_type, query,
                               f"title->doi ({chosen['source']})"))
        # Matched candidate had no identifier — we cannot reliably fetch the content.
        raise PaperFetchError(query, [("title_resolved_no_identifier", chosen.get("title") or "")])
    except PaperAlreadyExistsError:
        # Already logged in _guard_existing
        raise
    except PaperFetchError as e:
        # Log the fetch error
        attempts_str = "; ".join(f"{src}: {reason}" for src, reason in e.attempts)
        storage.log_fetch(category_slug, query, c.input_type, "error",
                         error=f"no_results: {attempts_str}")
        raise
    except Exception as e:
        # Catch-all for unexpected errors
        storage.log_fetch(category_slug, query, c.input_type, "error",
                         error=f"{type(e).__name__}: {e}")
        raise
