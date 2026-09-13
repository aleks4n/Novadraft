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
from typing import Literal

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
    """Directory a given agent's run logs are written to, for a workspace.

    A finished run can be reopened from history by reading its file back."""
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


def list_paper_ids(category_slug: str) -> list[str]:
    """Every paper in the workspace, by id.

    One directory per paper — the pdf and the extracted source live beside the record —
    so the id is the directory name and a `*.json` glob at the top of `papers/` matches
    nothing. Only directories that actually hold a `paper.json` count: a fetch that died
    after `mkdir` leaves a folder behind, and listing it would name a paper nothing can
    read."""
    d = papers_dir(category_slug)
    if not d.exists():
        return []
    return sorted(p.name for p in d.iterdir() if (p / "paper.json").exists())


def paper_json_path(category_slug: str, paper_id: str) -> Path:
    """Where a paper's record lives. Exposed for callers that need the file's mtime to
    decide whether a derived index is stale, without reading the record itself."""
    return paper_dir(category_slug, paper_id) / "paper.json"


def fully_extracted_papers(category_slug: str) -> list[str]:
    """Papers whose PDF has been through extraction — the ones downstream stages can read.

    A paper exists on disk from the moment it is fetched, so `list_paper_ids` also names
    records that are still just metadata. Anything that needs the full text (summarize,
    gap extraction, a paper count shown to the researcher) wants this list instead."""
    pdir = papers_dir(category_slug)
    if not pdir.exists():
        return []
    out = []
    for d in sorted(pdir.iterdir()):
        rec = read_paper_json(category_slug, d.name)
        if rec and rec.get("processing", {}).get("extracted"):
            out.append(d.name)
    return out


def list_workspaces() -> list[dict]:
    """All categories under the workspace root, with their extracted-paper count.

    A directory counts as a workspace only once it has a `meta.json` (the record the
    category agent writes); leading-underscore names are internal caches, not topics —
    e.g. the validator's `_validator_cache`."""
    root = workspace_root()
    if not root.exists():
        return []
    out = []
    for d in sorted(root.iterdir()):
        if d.name.startswith("_"):
            continue
        if not d.is_dir() or not (d / "meta.json").exists():
            continue
        out.append({
            "category_slug": d.name,
            "n_extracted_papers": len(fully_extracted_papers(d.name)),
        })
    return out


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


# --- gaps/<stage>/<gap_id>.json ----------------------------------------------
# Gaps move through stage folders as they flow down the pipeline; each stage's
# owner drains its inbox and writes into the next:
#   pending/      gap agent's output (freshly extracted)
#   to_validate/  validator inbox (the director drops novel gaps here)
#   to_rank/      ranking inbox (the validator drops open / partially-solved gaps)
#   master/       the master gap list (validated, live opportunities — each carries
#                 a ranking)
#   archive/      retired gaps kept for the record: the director parks duplicates
#                 here after merging their evidence into the matching master gap,
#                 and the validator parks solved gaps here (already addressed by
#                 prior art, so not an opportunity)

GapStage = Literal["pending", "to_validate", "to_rank", "master", "archive"]

# Pipeline order — also the search order used to locate where a gap currently lives.
GAP_STAGES: tuple[GapStage, ...] = ("pending", "to_validate", "to_rank", "master", "archive")


def gap_stage_dir(category_slug: str, stage: GapStage) -> Path:
    return category_dir(category_slug) / "gaps" / stage


def gap_json_path(category_slug: str, gap_id: str, stage: GapStage) -> Path:
    """Where a gap's record lives, in a given stage.

    Exposed for callers that need the file's mtime to decide whether a derived index is
    stale, without reading the record itself."""
    return gap_stage_dir(category_slug, stage) / f"{gap_id}.json"


def read_gap_json(category_slug: str, gap_id: str, stage: GapStage) -> dict | None:
    p = gap_stage_dir(category_slug, stage) / f"{gap_id}.json"
    if not p.exists():
        return None
    return json.loads(p.read_text())


def write_gap_json(category_slug: str, record: dict, stage: GapStage) -> Path:
    d = gap_stage_dir(category_slug, stage)
    d.mkdir(parents=True, exist_ok=True)
    out = d / f"{record['gap_id']}.json"
    out.write_text(json.dumps(record, indent=2, ensure_ascii=False))
    return out


def remove_gap_json(category_slug: str, gap_id: str, stage: GapStage) -> None:
    (gap_stage_dir(category_slug, stage) / f"{gap_id}.json").unlink(missing_ok=True)


def list_gap_ids(category_slug: str, stage: GapStage) -> list[str]:
    d = gap_stage_dir(category_slug, stage)
    if not d.exists():
        return []
    return sorted(p.stem for p in d.glob("*.json"))


def find_gap(category_slug: str, gap_id: str) -> tuple[GapStage | None, dict | None]:
    """Locate a gap by id across every stage folder, returning ``(stage, record)``.

    A gap lives in exactly one stage at a time (it is moved, not copied, as it
    flows down the pipeline), so the first hit in :data:`GAP_STAGES` order is
    authoritative. Returns ``(None, None)`` if the gap is nowhere on disk."""
    for stage in GAP_STAGES:
        rec = read_gap_json(category_slug, gap_id, stage)
        if rec is not None:
            return stage, rec
    return None, None


