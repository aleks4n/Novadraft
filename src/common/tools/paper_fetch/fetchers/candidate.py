"""Fallback fetcher for title-search candidates with no arXiv id or DOI.

Used when a search candidate (Semantic Scholar / OpenAlex) has neither an
arxiv_id nor a doi, but does have an open-access PDF URL. Builds the paper
record from the search metadata directly and downloads that PDF.
"""
from __future__ import annotations

from ....utils import storage
from ..http_util import download_to


def fetch_candidate(candidate: dict, destination: str, classified_type: str, query: str) -> dict:
    paper_id = storage.title_paper_id(
        candidate.get("title") or query,
        (candidate.get("authors") or [None])[0],
    )
    record = storage.new_paper_record(paper_id, classified_type, query)
    md = record["metadata"]
    src = record["source"]

    md["title"] = candidate.get("title")
    md["authors"] = candidate.get("authors") or []
    md["year"] = candidate.get("year")
    md["venue"] = candidate.get("venue")
    md["identifiers"]["semantic_scholar_id"] = candidate.get("semantic_scholar_id")

    pdf_url = candidate.get("pdf_url")
    src["resolved_via"] = f"title_search_pdf ({candidate.get('source')})"
    src["source_url"] = pdf_url

    pdir = storage.paper_dir(destination, paper_id)
    pdir.mkdir(parents=True, exist_ok=True)

    if pdf_url:
        try:
            download_to(pdf_url, pdir / "source.pdf")
            src["files"]["pdf"] = "source.pdf"
        except Exception as e:
            record["warnings"].append(f"pdf_download_failed: {e!r}")
    else:
        record["warnings"].append("no_pdf_url")

    storage.write_paper_json(destination, record)
    return record
