"""Paper full-text fetch (INTERIM — to be replaced by survey_gap's downloader).

Downloads a paper's LaTeX source from arXiv and concatenates its `.tex` files.
PDF-only submissions are skipped (return None) — the real downloader will handle
those; deep-read just backfills from the next-ranked paper. Caches under
`<repo>/.cache/arxiv/` so repeated dev runs don't re-hit arXiv.
"""

from __future__ import annotations

import io
import tarfile
from pathlib import Path
from typing import Optional

import requests

_CACHE_DIR = Path(__file__).resolve().parents[3] / ".cache" / "arxiv"
_HEADERS = {"User-Agent": "novadraft-validator/0.1 (research gap validation)"}


def _bare_id(arxiv_id: str) -> str:
    """`arxiv_2201.11903` -> `2201.11903` (the downloader wants the bare id)."""
    return arxiv_id.removeprefix("arxiv_")


def _extract_tex(tar_bytes: bytes) -> Optional[str]:
    """Concatenate every .tex member of a gzipped tar. None if not a readable tar."""
    try:
        parts = []
        with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:gz") as tar:
            for member in tar.getmembers():
                if member.name.endswith(".tex"):
                    f = tar.extractfile(member)
                    if f is not None:
                        parts.append(f.read().decode("utf-8", errors="replace"))
        return "\n\n".join(parts) or None
    except (tarfile.TarError, OSError):
        return None


def fetch_fulltext(arxiv_id: str) -> Optional[str]:
    """Return a paper's LaTeX full text, or None if unavailable (PDF-only / error)."""
    bare = _bare_id(arxiv_id)
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cached = _CACHE_DIR / f"{bare}.txt"
    if cached.exists():
        return cached.read_text(encoding="utf-8")

    try:
        resp = requests.get(f"https://arxiv.org/src/{bare}", headers=_HEADERS, timeout=30)
    except requests.RequestException:
        return None
    if resp.status_code != 200 or resp.content[:4] == b"%PDF":
        return None  # error, or PDF-only submission (deferred to the real downloader)

    text = _extract_tex(resp.content)
    if text:
        cached.write_text(text, encoding="utf-8")
    return text
