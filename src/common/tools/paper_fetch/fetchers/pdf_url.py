"""Direct PDF URL fetcher with arxiv upgrade chain.

Chain (each step falls through to the next on failure):
  1. arXiv ID in PDF Info dict         → fetch_arxiv
  2. DOI in PDF Info dict + S2 lookup  → fetch_arxiv (if found) else fetch_doi
  3. Title in PDF Info dict + S2 search→ fetch_arxiv (if found) else fetch_doi
  4. No match                          → store PDF as-is
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from ....utils import storage
from ....utils.trace import log_event
from ..errors import PaperAlreadyExistsError
from ..http_util import download_to
from ..pdf_metadata import extract_pdf_identifiers, extract_pdf_metadata
from .arxiv import fetch_arxiv
from .arxiv_lookup import arxiv_id_for_doi, arxiv_id_for_title
from .doi import fetch_doi


def _guard(destination: str, paper_id: str, query: str, input_type: str) -> None:
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


def _try_arxiv(arxiv_id: str, destination: str, classified_type: str, query: str) -> dict:
    paper_id = storage.arxiv_paper_id(arxiv_id)
    _guard(destination, paper_id, query, classified_type)
    return fetch_arxiv(arxiv_id, destination, classified_type, query)


def _try_doi(doi: str, destination: str, classified_type: str, query: str,
             fallback_pdf_url: str | None = None) -> dict:
    paper_id = storage.doi_paper_id(doi)
    _guard(destination, paper_id, query, classified_type)
    return fetch_doi(doi, destination, classified_type, query, fallback_pdf_url=fallback_pdf_url)


def fetch_pdf_url(url: str, destination: str, classified_type: str, query: str) -> dict:
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_pdf = Path(tmpdir) / "probe.pdf"

        try:
            download_to(url, tmp_pdf)
        except Exception as e:
            paper_id = storage.title_paper_id(url)
            record = storage.new_paper_record(paper_id, classified_type, query)
            record["warnings"].append(f"pdf_download_failed: {e!r}")
            storage.paper_dir(destination, paper_id).mkdir(parents=True, exist_ok=True)
            storage.write_paper_json(destination, record)
            return record

        try:
            identifiers = extract_pdf_identifiers(tmp_pdf)
        except Exception:
            identifiers = {}

        try:
            meta = extract_pdf_metadata(tmp_pdf)
        except Exception:
            meta = {}

        # 1. arXiv ID directly in PDF metadata
        if identifiers.get("arxiv_id"):
            log_event("found arxiv id in pdf metadata, fetching via arxiv",
                      arxiv_id=identifiers["arxiv_id"])
            return _try_arxiv(identifiers["arxiv_id"], destination, classified_type, query)

        # 2. DOI in PDF metadata → try S2 for arXiv upgrade, else DOI fetcher
        if identifiers.get("doi"):
            doi = identifiers["doi"]
            found_arxiv = arxiv_id_for_doi(doi)
            if found_arxiv:
                log_event("found doi in pdf metadata, resolved to an arxiv id, fetching via arxiv",
                          doi=doi, arxiv_id=found_arxiv)
                return _try_arxiv(found_arxiv, destination, classified_type, query)
            log_event("found doi in pdf metadata (no arxiv mirror), fetching via doi", doi=doi)
            return _try_doi(doi, destination, classified_type, query, fallback_pdf_url=url)

        # 3. Title from PDF metadata → S2 title search
        if meta.get("title"):
            found_arxiv, found_doi = arxiv_id_for_title(meta["title"])
            if found_arxiv:
                log_event("found title in pdf metadata, matched an arxiv id, fetching via arxiv",
                          title=meta["title"], arxiv_id=found_arxiv)
                return _try_arxiv(found_arxiv, destination, classified_type, query)
            if found_doi:
                log_event("found title in pdf metadata, matched a doi (no arxiv mirror), fetching via doi",
                          title=meta["title"], doi=found_doi)
                return _try_doi(found_doi, destination, classified_type, query, fallback_pdf_url=url)

        # 4. No match — keep PDF, carry over whatever metadata we have
        log_event("no arxiv/doi/title match in pdf metadata, keeping pdf as-is")
        paper_id = storage.title_paper_id(url)
        pdir = storage.paper_dir(destination, paper_id)
        pdir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(tmp_pdf, pdir / "source.pdf")

        record = storage.new_paper_record(paper_id, classified_type, query)
        src = record["source"]
        src["resolved_via"] = "direct_pdf"
        src["source_url"] = url
        src["files"]["pdf"] = "source.pdf"

        md = record["metadata"]
        for field in ("title", "authors", "year"):
            if meta.get(field):
                md[field] = meta[field]

        storage.write_paper_json(destination, record)
        return record
