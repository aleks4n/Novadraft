"""arXiv fetcher: metadata via export API, tex via e-print, plus PDF."""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

from ....utils import storage
from ..date_util import iso_from_text, year_of
from ..http_util import download_to, extract_archive, get_bytes, get_text

_NS = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}


def _parse_atom(xml_text: str) -> dict:
    root = ET.fromstring(xml_text)
    entry = root.find("a:entry", _NS)
    if entry is None:
        return {}
    title = entry.findtext("a:title", default="", namespaces=_NS).strip()
    summary = entry.findtext("a:summary", default="", namespaces=_NS).strip()
    published = entry.findtext("a:published", default="", namespaces=_NS).strip()
    authors = [
        a.findtext("a:name", default="", namespaces=_NS).strip()
        for a in entry.findall("a:author", _NS)
    ]
    doi = entry.findtext("arxiv:doi", default=None, namespaces=_NS)
    journal_ref = entry.findtext("arxiv:journal_ref", default=None, namespaces=_NS)
    publication_date = iso_from_text(published)
    return {
        "title": re.sub(r"\s+", " ", title),
        "abstract": summary,
        "authors": authors,
        "publication_date": publication_date,
        "year": year_of(publication_date),
        "doi": doi,
        "venue": journal_ref,
    }


def fetch_arxiv(arxiv_id: str, destination: str, classified_type: str, query: str) -> dict:
    """Fetch an arXiv paper. Returns the paper.json dict (written to disk)."""
    paper_id = storage.arxiv_paper_id(arxiv_id)
    record = storage.new_paper_record(paper_id, classified_type, query)
    md = record["metadata"]
    src = record["source"]
    md["identifiers"]["arxiv_id"] = arxiv_id
    src["resolved_via"] = "arxiv"
    src["source_url"] = f"https://arxiv.org/abs/{arxiv_id}"

    # Metadata
    try:
        xml = get_text(f"https://export.arxiv.org/api/query?id_list={arxiv_id}")
        meta = _parse_atom(xml)
        md["title"] = meta.get("title") or md["title"]
        md["abstract"] = meta.get("abstract") or md["abstract"]
        md["authors"] = meta.get("authors") or md["authors"]
        md["publication_date"] = meta.get("publication_date")
        md["year"] = meta.get("year")
        if meta.get("doi"):
            md["identifiers"]["doi"] = meta["doi"]
        if meta.get("venue"):
            md["venue"] = meta["venue"]
    except Exception as e:
        record["warnings"].append(f"arxiv_metadata_failed: {e!r}")

    pdir = storage.paper_dir(destination, paper_id)
    pdir.mkdir(parents=True, exist_ok=True)

    # PDF
    try:
        pdf_path = pdir / "source.pdf"
        download_to(f"https://arxiv.org/pdf/{arxiv_id}.pdf", pdf_path)
        src["files"]["pdf"] = "source.pdf"
    except Exception as e:
        record["warnings"].append(f"pdf_download_failed: {e!r}")

    # Tex (e-print)
    try:
        body, _ = get_bytes(f"https://arxiv.org/e-print/{arxiv_id}")
        tex_dir = pdir / "source"
        if extract_archive(body, tex_dir):
            src["files"]["tex_dir"] = "source"
            main_tex = _guess_main_tex(tex_dir)
            if main_tex:
                src["files"]["tex_main"] = f"source/{main_tex.name}"
        else:
            record["warnings"].append("tex_archive_unrecognized_format")
    except Exception as e:
        record["warnings"].append(f"tex_download_failed: {e!r}")

    storage.write_paper_json(destination, record)
    return record


def _guess_main_tex(tex_dir: Path) -> Path | None:
    candidates = list(tex_dir.rglob("*.tex"))
    if not candidates:
        return None
    # Prefer files containing \documentclass
    for p in candidates:
        try:
            head = p.read_text(errors="ignore")[:4000]
            if r"\documentclass" in head:
                return p
        except OSError:
            continue
    return candidates[0]
