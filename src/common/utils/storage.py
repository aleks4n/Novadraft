"""Filesystem layout + paper.json writer for the per-category workspace.

Layout:
    workspace/<category>/
      meta.json
      papers/<paper_id>/
        paper.json
        source.pdf
        source/...   (tex tree, optional)
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

WORKSPACE_ENV = "NOVADRAFT_WORKSPACE"
_DEFAULT_WORKSPACE = Path(__file__).resolve().parents[3] / "workspace"


def workspace_root() -> Path:
    return Path(os.environ.get(WORKSPACE_ENV, _DEFAULT_WORKSPACE))


def category_dir(category_slug: str) -> Path:
    return workspace_root() / category_slug


def papers_dir(category_slug: str) -> Path:
    return category_dir(category_slug) / "papers"


def paper_dir(category_slug: str, paper_id: str) -> Path:
    return papers_dir(category_slug) / paper_id


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def ensure_category(category_slug: str) -> Path:
    """Verify the category exists. The fetch agent does not create categories."""
    d = category_dir(category_slug)
    if not (d / "meta.json").exists():
        raise FileNotFoundError(
            f"Category '{category_slug}' not found at {d}. "
            f"Create it first with a meta.json."
        )
    return d


# --- ID generation -----------------------------------------------------------

def arxiv_paper_id(arxiv_id: str) -> str:
    # strip version suffix
    bare = re.sub(r"v\d+$", "", arxiv_id, flags=re.IGNORECASE)
    return f"arxiv_{bare.replace('/', '_')}"


def doi_paper_id(doi: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", doi).strip("_").lower()
    return f"doi_{slug}"


def title_paper_id(title: str, first_author: str | None = None) -> str:
    norm = re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()
    seed = f"{norm}|{(first_author or '').lower()}"
    return f"hash_{hashlib.sha1(seed.encode()).hexdigest()[:8]}"


# --- paper.json --------------------------------------------------------------

def new_paper_record(paper_id: str, classified_type: str, query: str) -> dict:
    return {
        "paper_id": paper_id,
        "metadata": {
            "title": None,
            "authors": [],
            "year": None,
            "venue": None,
            "abstract": None,
            "identifiers": {
                "arxiv_id": None,
                "doi": None,
                "semantic_scholar_id": None,
                "openreview_id": None,
            },
        },
        "source": {
            "input_query": query,
            "input_type": classified_type,
            "resolved_via": None,
            "source_url": None,
            "files": {
                "pdf": None,
                "tex_dir": None,
                "tex_main": None,
            },
            "fetched_at": now_iso(),
        },
        "content": None,
        "summary": None,
        "gaps": {"gap_ids": []},
        "processing": {
            "fetched": True,
            "extracted": False,
            "summarized": False,
            "gaps_extracted": False,
        },
        "warnings": [],
    }


def write_paper_json(category_slug: str, record: dict) -> Path:
    pdir = paper_dir(category_slug, record["paper_id"])
    pdir.mkdir(parents=True, exist_ok=True)
    out = pdir / "paper.json"
    out.write_text(json.dumps(record, indent=2, ensure_ascii=False))
    return out


def paper_exists(category_slug: str, paper_id: str) -> bool:
    return (paper_dir(category_slug, paper_id) / "paper.json").exists()


def read_paper_json(category_slug: str, paper_id: str) -> dict | None:
    p = paper_dir(category_slug, paper_id) / "paper.json"
    if not p.exists():
        return None
    return json.loads(p.read_text())


# --- Logging -------------------------------------------------------------------

def log_fetch(category_slug: str, query: str, input_type: str, outcome: str,
              paper_id: str | None = None, error: str | None = None, extra: dict | None = None):
    """Log a fetch attempt to fetch_log.jsonl.

    outcome: "success", "ambiguous", "error", "duplicate"
    """
    log_path = category_dir(category_slug) / "fetch_log.jsonl"
    entry = {
        "timestamp": now_iso(),
        "query": query,
        "input_type": input_type,
        "outcome": outcome,
        "paper_id": paper_id,
        "error": error,
        **(extra or {}),
    }
    with open(log_path, "a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
