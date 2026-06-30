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


def ideas_dir(category_slug: str) -> Path:
    return category_dir(category_slug) / "ideas"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def agent_log_dir(category_slug: str, agent: str) -> Path:
    """Directory a given agent's run-tree logs are written to, for a workspace.

    The dev UI assembles each streamed run into a tree and persists it here so a
    finished run can be reopened from history (see dev/ui.py)."""
    return category_dir(category_slug) / "logs" / agent


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
            "publication_date": None,
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
        "summary_verification": None,
        "gaps": {"gap_ids": []},
        "processing": {
            "fetched": True,
            "extracted": False,
            "summarized": False,
            "summary_verified": False,
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


# --- ideas/<idea_id>.json ----------------------------------------------------

def write_idea_json(category_slug: str, record: dict) -> Path:
    d = ideas_dir(category_slug)
    d.mkdir(parents=True, exist_ok=True)
    out = d / f"{record['idea_id']}.json"
    out.write_text(json.dumps(record, indent=2, ensure_ascii=False))
    return out


def read_idea_json(category_slug: str, idea_id: str) -> dict | None:
    p = ideas_dir(category_slug) / f"{idea_id}.json"
    if not p.exists():
        return None
    return json.loads(p.read_text())


def list_idea_ids(category_slug: str) -> list[str]:
    d = ideas_dir(category_slug)
    if not d.exists():
        return []
    return sorted(p.stem for p in d.glob("*.json"))


# --- verdicts/<gap_id>.json --------------------------------------------------
# One validator Verdict per gap, keyed by gap_id — the handoff the ranking agent reads.

def verdicts_dir(category_slug: str) -> Path:
    return category_dir(category_slug) / "verdicts"


def write_verdict_json(category_slug: str, record: dict) -> Path:
    d = verdicts_dir(category_slug)
    d.mkdir(parents=True, exist_ok=True)
    out = d / f"{record['gap_id']}.json"
    out.write_text(json.dumps(record, indent=2, ensure_ascii=False))
    return out


def read_verdict_json(category_slug: str, gap_id: str) -> dict | None:
    p = verdicts_dir(category_slug) / f"{gap_id}.json"
    if not p.exists():
        return None
    return json.loads(p.read_text())


def list_verdict_ids(category_slug: str) -> list[str]:
    d = verdicts_dir(category_slug)
    if not d.exists():
        return []
    return sorted(p.stem for p in d.glob("*.json"))


