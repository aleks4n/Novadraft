"""PaperFetchAgent entry point — a common tool, callable for any destination.

Returns one of:
  - {"status": "ok",        "paper": <paper.json dict>, "log_entry": {...}}
  - {"status": "ambiguous", "candidates": [...], "message": "...", "log_entry": {...}}

Raises:
  - PaperFetchError          — nothing found
  - PaperAlreadyExistsError  — paper already in the workspace

In both the returned dicts and the raised exceptions, a ``log_entry`` is
attached describing the fetch attempt; progress notes stream into the caller's
``fetch`` graph step via ``log_event`` (see ``src/common/utils/trace.py``).
"""
from __future__ import annotations

from ...utils import storage
from ...utils.logging.trace import log_event
from .classify import classify
from .errors import PaperAlreadyExistsError, PaperFetchError
from .fetchers.arxiv import fetch_arxiv
from .fetchers.arxiv_lookup import arxiv_id_for_doi
from .fetchers.candidate import fetch_candidate
from .fetchers.doi import fetch_doi
from .fetchers.landing import resolve_landing
from .fetchers.pdf_url import fetch_pdf_url
from .fetchers.title import resolve_title
from .pdf_metadata import extract_pdf_metadata

_CORE_METADATA_FIELDS = ("title", "authors", "year")


def _success_log_entry(rec: dict, query: str, input_type: str, source: str) -> dict:
    md = rec["metadata"]
    return {
        "query": query,
        "input_type": input_type,
        "outcome": "success",
        "paper_id": rec["paper_id"],
        "extra": {
            "source": source,
            "title": md.get("title"),
            "authors": md.get("authors", [])[:3],
            "year": md.get("year"),
            "publication_date": md.get("publication_date"),
            "arxiv_id": md["identifiers"].get("arxiv_id"),
            "doi": md["identifiers"].get("doi"),
        },
    }


def _ok(rec: dict, query: str, input_type: str, source: str) -> dict:
    return {"status": "ok", "paper": rec, "log_entry": _success_log_entry(rec, query, input_type, source)}


def _ambiguous(candidates: list[dict], query: str, input_type: str) -> dict:
    summarized = [
        {
            "title": c.get("title"),
            "authors": c.get("authors") or [],
            "year": c.get("year"),
            "publication_date": c.get("publication_date"),
            "arxiv_id": c.get("arxiv_id"),
            "doi": c.get("doi"),
            "pdf_url": c.get("pdf_url"),
            "source": c.get("source"),
        }
        for c in candidates
    ]
    return {
        "status": "ambiguous",
        "message": (
            f"No exact match for {query!r}. Pick one by re-running with its arXiv ID or DOI."
        ),
        "candidates": summarized,
        "log_entry": {
            "query": query,
            "input_type": input_type,
            "outcome": "ambiguous",
            "extra": {"num_candidates": len(candidates), "candidates": summarized},
        },
    }


def _fill_missing_metadata(record: dict, destination: str) -> bool:
    """Best-effort fill of missing title/authors/year from the downloaded PDF.

    Returns True if the record was modified (so the caller can persist it).
    """
    md = record["metadata"]
    missing = [f for f in _CORE_METADATA_FIELDS if not md.get(f)]
    if not missing:
        return False

    pdf_rel = record["source"]["files"].get("pdf")
    if not pdf_rel:
        record["warnings"].append(
            f"metadata_incomplete: missing {', '.join(missing)} (no pdf to extract from)"
        )
        return True

    pdf_path = storage.paper_dir(destination, record["paper_id"]) / pdf_rel
    try:
        extracted = extract_pdf_metadata(pdf_path)
    except Exception as e:
        record["warnings"].append(f"pdf_metadata_extraction_failed: {e!r}")
        extracted = {}

    for field in missing:
        value = extracted.get(field)
        if value:
            md[field] = value

    # Carry the full date alongside a PDF-derived year (best-effort: this is
    # the PDF's creation date, used only when better sources gave nothing).
    if "year" in missing and not md.get("publication_date") and extracted.get("publication_date"):
        md["publication_date"] = extracted["publication_date"]

    still_missing = [f for f in missing if not md.get(f)]
    if still_missing:
        record["warnings"].append(
            f"metadata_incomplete: could not determine {', '.join(still_missing)} from pdf"
        )

    return True


def _check_usable(record: dict, query: str, source: str, destination: str) -> dict:
    if _fill_missing_metadata(record, destination):
        storage.write_paper_json(destination, record)

    has_pdf = bool(record["source"]["files"].get("pdf"))
    md = record["metadata"]
    has_meta = bool(md.get("title")) and any(md["identifiers"].values())
    if has_pdf or has_meta:
        return record
    reason = "; ".join(record.get("warnings") or []) or "no metadata or pdf"
    raise PaperFetchError(query, [(source, reason)])


def _guard_existing(destination: str, paper_id: str, query: str, input_type: str):
    if storage.paper_exists(destination, paper_id):
        err = PaperAlreadyExistsError(destination, paper_id)
        err.log_entry = {
            "query": query,
            "input_type": input_type,
            "outcome": "duplicate",
            "paper_id": paper_id,
            "error": "already exists",
        }
        raise err


def _do_arxiv(arxiv_id, destination, input_type, query):
    paper_id = storage.arxiv_paper_id(arxiv_id)
    _guard_existing(destination, paper_id, query, input_type)
    rec = fetch_arxiv(arxiv_id, destination, input_type, query)
    return rec


def _do_doi(doi, destination, input_type, query, fallback_pdf_url=None):
    paper_id = storage.doi_paper_id(doi)
    _guard_existing(destination, paper_id, query, input_type)
    rec = fetch_doi(doi, destination, input_type, query, fallback_pdf_url=fallback_pdf_url)
    return rec


def _do_candidate(chosen, destination, input_type, query):
    first_author = (chosen.get("authors") or [None])[0]
    paper_id = storage.title_paper_id(chosen.get("title") or query, first_author)
    _guard_existing(destination, paper_id, query, input_type)
    rec = fetch_candidate(chosen, destination, input_type, query)
    return rec


def _do_pdf(url, destination, input_type, query):
    paper_id = storage.title_paper_id(url)
    record = storage.read_paper_json(destination, paper_id)
    if record is not None:
        resolved_via = (record.get("source") or {}).get("resolved_via")
        pdf_rel = (record.get("source") or {}).get("files", {}).get("pdf")
        pdf_path = storage.paper_dir(destination, paper_id) / pdf_rel if pdf_rel else None
        is_invalid_direct_pdf = (
            resolved_via == "direct_pdf"
            and pdf_path is not None
            and pdf_path.exists()
            and not pdf_path.read_bytes()[:5] == b"%PDF-"
        )
        if not is_invalid_direct_pdf:
            _guard_existing(destination, paper_id, query, input_type)
    rec = fetch_pdf_url(url, destination, input_type, query)
    return rec


def fetch(destination: str, query: str) -> dict:
    storage.ensure_category(destination)
    c = classify(query)
    log_event("classified query", query=query, input_type=c.input_type, value=c.value)

    try:
        if c.input_type in ("arxiv_id", "arxiv_url"):
            source = "arxiv"
            log_event("fetching via arxiv", arxiv_id=c.value)
            rec = _do_arxiv(c.value, destination, c.input_type, query)
            _check_usable(rec, query, source, destination)
            return _ok(rec, query, c.input_type, source)

        if c.input_type in ("doi", "doi_url"):
            arxiv_id = arxiv_id_for_doi(c.value)
            if arxiv_id:
                source = "doi->arxiv"
                log_event("doi resolved to an arxiv id, fetching via arxiv",
                          doi=c.value, arxiv_id=arxiv_id)
                rec = _do_arxiv(arxiv_id, destination, c.input_type, query)
            else:
                source = "doi"
                log_event("fetching via doi (no arxiv mirror)", doi=c.value)
                rec = _do_doi(c.value, destination, c.input_type, query)
            _check_usable(rec, query, source, destination)
            return _ok(rec, query, c.input_type, source)

        if c.input_type == "pdf_url":
            source = "pdf_url"
            log_event("fetching pdf directly from url", url=c.value)
            rec = _do_pdf(c.value, destination, c.input_type, query)
            _check_usable(rec, query, source, destination)
            return _ok(rec, query, c.input_type, source)

        if c.input_type in ("landing_url", "url"):
            log_event("resolving landing page", url=c.value)
            resolved = resolve_landing(c.value)
            if "arxiv_id" in resolved:
                source = "landing->arxiv"
                log_event("landing page resolved to an arxiv id", arxiv_id=resolved["arxiv_id"])
                rec = _do_arxiv(resolved["arxiv_id"], destination, c.input_type, query)
            elif "doi" in resolved:
                doi = resolved["doi"]
                arxiv_id = arxiv_id_for_doi(doi)
                if arxiv_id:
                    source = "landing->doi->arxiv"
                    log_event("landing page resolved to a doi with an arxiv mirror",
                              doi=doi, arxiv_id=arxiv_id)
                    rec = _do_arxiv(arxiv_id, destination, c.input_type, query)
                else:
                    source = "landing->doi"
                    log_event("landing page resolved to a doi (no arxiv mirror)", doi=doi)
                    rec = _do_doi(doi, destination, c.input_type, query)
            elif "pdf_url" in resolved:
                source = "landing->pdf"
                log_event("landing page resolved to a pdf url", pdf_url=resolved["pdf_url"])
                rec = _do_pdf(resolved["pdf_url"], destination, c.input_type, query)
            else:
                source = "url_as_pdf"
                log_event("landing page did not resolve, treating url as a pdf directly")
                rec = _do_pdf(c.value, destination, c.input_type, query)
            _check_usable(rec, query, source, destination)
            return _ok(rec, query, c.input_type, source)

        # Title path: search → match-or-disambiguate
        log_event("searching by title", title=c.value)
        chosen, candidates = resolve_title(c.value, query)
        if chosen is None:
            log_event("no exact title match, returning candidates for disambiguation",
                      n_candidates=len(candidates))
            return _ambiguous(candidates, query, c.input_type)

        if chosen.get("arxiv_id"):
            source = f"title->arxiv ({chosen['source']})"
            log_event("title search matched an arxiv id", arxiv_id=chosen["arxiv_id"],
                      via=chosen["source"])
            rec = _do_arxiv(chosen["arxiv_id"], destination, c.input_type, query)
        elif chosen.get("doi"):
            doi = chosen["doi"]
            arxiv_id = arxiv_id_for_doi(doi)
            if arxiv_id:
                source = f"title->doi->arxiv ({chosen['source']})"
                log_event("title search matched a doi with an arxiv mirror",
                          doi=doi, arxiv_id=arxiv_id, via=chosen["source"])
                rec = _do_arxiv(arxiv_id, destination, c.input_type, query)
            else:
                source = f"title->doi ({chosen['source']})"
                log_event("title search matched a doi (no arxiv mirror)",
                          doi=doi, via=chosen["source"])
                rec = _do_doi(doi, destination, c.input_type, query,
                               fallback_pdf_url=chosen.get("pdf_url"))
        elif chosen.get("pdf_url"):
            source = f"title->pdf ({chosen['source']})"
            log_event("title search matched a pdf url", pdf_url=chosen["pdf_url"],
                      via=chosen["source"])
            rec = _do_candidate(chosen, destination, c.input_type, query)
        else:
            # Matched candidate had no identifier or PDF — we cannot reliably fetch the content.
            raise PaperFetchError(query, [("title_resolved_no_identifier", chosen.get("title") or "")])

        _check_usable(rec, query, source, destination)
        return _ok(rec, query, c.input_type, source)
    except PaperAlreadyExistsError:
        # log_entry already attached in _guard_existing
        log_event("paper already exists in this workspace")
        raise
    except PaperFetchError as e:
        attempts_str = "; ".join(f"{src}: {reason}" for src, reason in e.attempts)
        log_event("all fetchers failed", attempts=attempts_str)
        e.log_entry = {
            "query": query,
            "input_type": c.input_type,
            "outcome": "error",
            "error": f"no_results: {attempts_str}",
        }
        raise
    except Exception as e:
        # Catch-all for unexpected errors
        log_event("unexpected error", error=f"{type(e).__name__}: {e}")
        e.log_entry = {
            "query": query,
            "input_type": c.input_type,
            "outcome": "error",
            "error": f"{type(e).__name__}: {e}",
        }
        raise
