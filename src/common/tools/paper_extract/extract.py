"""PaperExtractAgent entry point.

Returns:
    {"status": "ok",      "paper_id": ..., "extracted_via": "tex"|"pdf"}
    {"status": "skipped", "paper_id": ..., "reason": "already_extracted"}
    {"status": "skipped", "paper_id": ..., "reason": "no_source_files"}
"""
from __future__ import annotations

import json
from pathlib import Path

from . import pdf_path
from ...utils import storage
from ...utils.trace import log_event
from . import tex_path


class PaperNotFoundError(Exception):
    pass


def _write(category_slug: str, record: dict) -> None:
    pdir = storage.paper_dir(category_slug, record["paper_id"])
    (pdir / "paper.json").write_text(json.dumps(record, indent=2, ensure_ascii=False))


def extract(category_slug: str, paper_id: str, force: bool = False) -> dict:
    record = storage.read_paper_json(category_slug, paper_id)
    if record is None:
        raise PaperNotFoundError(f"{category_slug}/{paper_id}")

    if record.get("processing", {}).get("extracted") and not force:
        log_event("already extracted, skipping", paper_id=paper_id)
        return {"status": "skipped", "paper_id": paper_id, "reason": "already_extracted"}

    pdir = storage.paper_dir(category_slug, paper_id)
    files = (record.get("source", {}) or {}).get("files", {}) or {}

    tex_dir = files.get("tex_dir")
    tex_main = files.get("tex_main")
    pdf = files.get("pdf")

    result = None
    via = None
    if tex_dir and tex_main and (pdir / tex_main).exists():
        log_event("extracting from latex source", tex_main=tex_main)
        result = tex_path.extract_tex(pdir / tex_main, pdir / tex_dir)
        via = "tex"
    elif pdf and (pdir / pdf).exists():
        log_event("no latex source available, extracting from pdf", pdf=pdf)
        result = pdf_path.extract_pdf(pdir / pdf)
        via = "pdf"
    else:
        log_event("no source files to extract from, skipping")
        record.setdefault("warnings", []).append("extraction_skipped: no source files")
        _write(category_slug, record)
        return {"status": "skipped", "paper_id": paper_id, "reason": "no_source_files"}

    record["content"] = {
        "sections": result["sections"],
        "full_text": result["full_text"],
        "tables": result.get("tables", []),
        "bibliography": result.get("bibliography", {}),
        "extraction": {
            "extracted_via": via,
            "format": "latex_cleaned" if via == "tex" else "markdown",
            "math_fidelity": "high" if via == "tex" else "low",
            "section_count": len(result["sections"]),
            "unresolved_macros": result.get("unresolved_macros", []),
            "unmapped_headings": result.get("unmapped_headings", []),
            "warnings": result.get("warnings", []),
        },
    }
    if not record.get("metadata", {}).get("abstract"):
        abstract = result["sections"].get("abstract")
        if abstract:
            record["metadata"]["abstract"] = abstract

    record["processing"]["extracted"] = True
    _write(category_slug, record)
    log_event("extraction complete", via=via, section_count=len(result["sections"]))
    return {
        "status": "ok",
        "paper_id": paper_id,
        "extracted_via": via,
        "section_count": len(result["sections"]),
    }
