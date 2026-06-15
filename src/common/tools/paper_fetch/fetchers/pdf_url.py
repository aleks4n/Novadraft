"""Direct PDF URL fetcher: download, no metadata."""
from __future__ import annotations

from ....utils import storage
from ..http_util import download_to


def fetch_pdf_url(url: str, destination: str, classified_type: str, query: str) -> dict:
    # Title unknown; ID derived from URL
    paper_id = storage.title_paper_id(url)
    record = storage.new_paper_record(paper_id, classified_type, query)
    src = record["source"]
    src["resolved_via"] = "direct_pdf"
    src["source_url"] = url

    pdir = storage.paper_dir(destination, paper_id)
    pdir.mkdir(parents=True, exist_ok=True)

    try:
        download_to(url, pdir / "source.pdf")
        src["files"]["pdf"] = "source.pdf"
    except Exception as e:
        record["warnings"].append(f"pdf_download_failed: {e!r}")

    storage.write_paper_json(destination, record)
    return record
