"""PDF extraction path via marker-pdf.

Marker converts a PDF to Markdown with LaTeX math preserved; the markdown is then
split by headings into a flat section dict.

Install: ``pip install marker-pdf``. The first call downloads ~1-2GB of models;
subsequent calls are fast.
"""
from __future__ import annotations

import re
from pathlib import Path

from .canonical import KNOWN_SECTIONS, canonicalize


class MarkerNotInstalledError(RuntimeError):
    def __init__(self):
        super().__init__(
            "marker-pdf is required for PDF extraction. Install with:\n"
            "    pip install marker-pdf\n"
            "First run will download models (~1-2GB)."
        )


def _run_marker(pdf_path: Path) -> str:
    """Return markdown for the PDF.

    Invokes the `marker_single` CLI in a subprocess. Subprocess isolation
    avoids in-process model state issues (segfaults on macOS multiprocessing)
    and keeps marker's heavy imports out of our process when not needed.
    """
    import os
    import shutil
    import subprocess
    import sys
    import tempfile

    # Prefer the marker_single binary that sits next to the current Python
    # interpreter (i.e. same venv/conda env). Falls back to PATH lookup.
    env_bin = Path(sys.executable).parent / "marker_single"
    marker_bin = str(env_bin) if env_bin.exists() else shutil.which("marker_single")
    if marker_bin is None:
        raise MarkerNotInstalledError()

    with tempfile.TemporaryDirectory() as tmp:
        out_dir = Path(tmp)
        subprocess.run(
            [
                marker_bin,
                str(pdf_path),
                "--output_format", "markdown",
                "--output_dir", str(out_dir),
                "--disable_image_extraction",
                "--disable_multiprocessing",
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        # marker writes <out_dir>/<pdf_stem>/<pdf_stem>.md
        md_files = list(out_dir.rglob("*.md"))
        if not md_files:
            raise RuntimeError(f"marker produced no markdown for {pdf_path}")
        return md_files[0].read_text(errors="replace")


# --- markdown -> sections -----------------------------------------------------

_HEADING_RE = re.compile(r"^(#{1,3})\s+(.+?)\s*$", re.MULTILINE)
_TABLE_BLOCK_RE = re.compile(
    r"((?:^\|.*\|\s*$\n?)+)",
    re.MULTILINE,
)


def _extract_tables_md(md: str) -> tuple[str, list[dict]]:
    tables: list[dict] = []

    def _sub(m: re.Match) -> str:
        idx = len(tables) + 1
        tab_id = f"tab_{idx}"
        tables.append({"id": tab_id, "caption": None, "markdown": m.group(1).strip()})
        return f"\n[TABLE:{tab_id}]\n"

    return _TABLE_BLOCK_RE.sub(_sub, md), tables


def _split_sections_md(md: str) -> tuple[dict[str, str], list[str]]:
    sections: dict[str, str] = {}
    unmapped: list[str] = []

    matches = list(_HEADING_RE.finditer(md))
    if not matches:
        return {"body": md.strip()}, []

    # Drop title/author/affiliation front matter: marker emits these as
    # headings before the real paper sections start. Skip everything up to
    # the first heading we recognize (e.g. Abstract, Introduction).
    start_idx = next(
        (i for i, m in enumerate(matches) if canonicalize(m.group(2)) in KNOWN_SECTIONS),
        0,
    )
    matches = matches[start_idx:]

    for i, m in enumerate(matches):
        heading = m.group(2).strip()
        key = canonicalize(heading)
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(md)
        body = md[start:end].strip()
        if key in sections:
            sections[key] += "\n\n" + body
        else:
            sections[key] = body
        if key not in KNOWN_SECTIONS:
            unmapped.append(heading)

    return sections, unmapped


# Markdown reference list entries, e.g. "- [12] Author, Title, Venue, Year."
_BIB_ITEM_RE = re.compile(
    r"^[-*]\s*\[(\d+)\]\s*(.*?)(?=\n[-*]\s*\[\d+\]|\Z)",
    re.MULTILINE | re.DOTALL,
)


def _extract_bibliography_md(sections: dict[str, str]) -> dict:
    """Split the references section into individual entries."""
    refs_text = sections.get("references", "")
    if not refs_text:
        return {}

    items = list(_BIB_ITEM_RE.finditer(refs_text))
    if items:
        bib: dict[str, dict] = {}
        for m in items:
            raw = re.sub(r"\s+", " ", m.group(2)).strip()
            if raw:
                bib[f"ref_{m.group(1)}"] = {"raw": raw}
        return bib

    # Fallback: split by blank lines.
    entries = re.split(r"\n\s*\n", refs_text)
    bib = {}
    for i, entry in enumerate(entries, 1):
        raw = re.sub(r"\s+", " ", entry).strip()
        if not raw:
            continue
        bib[f"ref_{i}"] = {"raw": raw}
    return bib


_SPAN_ANCHOR_RE = re.compile(r"<span[^>]*></span>")


def _clean_marker_noise(md: str) -> str:
    md = _SPAN_ANCHOR_RE.sub("", md)
    md = re.sub(r"[ \t]+", " ", md)
    return md


def extract_pdf(pdf_path: Path) -> dict:
    md = _run_marker(pdf_path)
    md = _clean_marker_noise(md)
    md, tables = _extract_tables_md(md)
    sections, unmapped = _split_sections_md(md)
    bibliography = _extract_bibliography_md(sections)
    full_text = "\n\n".join(f"# {k}\n\n{v}" for k, v in sections.items())

    return {
        "sections": sections,
        "full_text": full_text,
        "tables": tables,
        "bibliography": bibliography,
        "unmapped_headings": sorted(set(unmapped)),
        "unresolved_macros": [],
        "warnings": [],
    }
