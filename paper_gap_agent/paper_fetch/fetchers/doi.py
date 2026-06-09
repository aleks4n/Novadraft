"""DOI fetcher: Crossref metadata + Unpaywall OA PDF."""
from __future__ import annotations

import re
from urllib.parse import quote

from .. import storage
from ..http_util import download_to, get_json

# Unpaywall requires a contact email; this is a non-routable placeholder.
# Override with NOVADRAFT_UNPAYWALL_EMAIL if you want polite-pool access.
import os
_UNPAYWALL_EMAIL = os.environ.get("NOVADRAFT_UNPAYWALL_EMAIL", "research@example.com")


def _crossref(doi: str) -> dict:
    data = get_json(f"https://api.crossref.org/works/{quote(doi, safe='/')}")
    msg = data.get("message", {})
    title = (msg.get("title") or [None])[0]
    authors = [
        f"{a.get('given','').strip()} {a.get('family','').strip()}".strip()
        for a in msg.get("author", [])
    ]
    year = None
    for key in ("published-print", "published-online", "issued"):
        parts = (msg.get(key) or {}).get("date-parts") or []
        if parts and parts[0]:
            year = parts[0][0]
            break
    venue = (msg.get("container-title") or [None])[0]
    return {
        "title": re.sub(r"\s+", " ", title or "").strip() or None,
        "authors": authors,
        "year": year,
        "venue": venue,
        "abstract": msg.get("abstract"),
    }


def _unpaywall_pdf(doi: str) -> str | None:
    try:
        data = get_json(
            f"https://api.unpaywall.org/v2/{quote(doi, safe='/')}?email={_UNPAYWALL_EMAIL}"
        )
    except Exception:
        return None
    best = data.get("best_oa_location") or {}
    return best.get("url_for_pdf") or None


def fetch_doi(doi: str, category_slug: str, classified_type: str, query: str) -> dict:
    paper_id = storage.doi_paper_id(doi)
    record = storage.new_paper_record(paper_id, classified_type, query)
    md = record["metadata"]
    src = record["source"]
    md["identifiers"]["doi"] = doi
    src["resolved_via"] = "crossref+unpaywall"
    src["source_url"] = f"https://doi.org/{doi}"

    try:
        meta = _crossref(doi)
        for k in ("title", "abstract", "year", "venue"):
            if meta.get(k):
                md[k] = meta[k]
        if meta.get("authors"):
            md["authors"] = meta["authors"]
    except Exception as e:
        record["warnings"].append(f"crossref_failed: {e!r}")

    pdir = storage.paper_dir(category_slug, paper_id)
    pdir.mkdir(parents=True, exist_ok=True)

    pdf_url = _unpaywall_pdf(doi)
    if pdf_url:
        try:
            download_to(pdf_url, pdir / "source.pdf")
            src["files"]["pdf"] = "source.pdf"
        except Exception as e:
            record["warnings"].append(f"oa_pdf_download_failed: {e!r}")
    else:
        record["warnings"].append("no_open_access_pdf")

    storage.write_paper_json(category_slug, record)
    return record
