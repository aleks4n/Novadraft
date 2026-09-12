"""Backfill venue + citation enrichment into papers already in the workspace.

New fetches are enriched inline (paper_fetch/fetch.py), but papers fetched before that
existed have `venue: None` and no citation count, which pins the ranking agent's impact
score to its neutral prior. This walks the workspace and fills them in.

    python dev/backfill_enrichment.py --dry-run          # report what would change
    python dev/backfill_enrichment.py llm_reasoning      # one category
    python dev/backfill_enrichment.py                    # every category

Only `<category>/papers/` is walked. Session and validator-cache papers are read-only
inputs to the validator and never reach the ranker, so enriching them would spend
API budget on records nothing scores.

No API key is needed. Papers are looked up in batches of 500, so a whole workspace is
one or two requests; unauthenticated Semantic Scholar rejects most individual attempts
but does so in well under a second, and the retry budget in enrich.py absorbs that
(measured: 5/5 runs fully enriched, 1-9s). Setting SEMANTIC_SCHOLAR_API_KEY just makes it
quieter. OPENALEX_MAILTO puts the fallback in OpenAlex's polite pool.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.common.tools.paper_fetch.enrich import enrich_records, s2_id_for  # noqa: E402
from src.common.utils import storage  # noqa: E402


def _categories(explicit: list[str]) -> list[str]:
    if explicit:
        return explicit
    root = storage.workspace_root()
    return sorted(
        d.name for d in root.iterdir()
        if d.is_dir() and not d.name.startswith((".", "_")) and (d / "papers").is_dir()
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("categories", nargs="*", help="category slugs (default: all)")
    ap.add_argument("--dry-run", action="store_true", help="report without writing")
    ap.add_argument("--force", action="store_true",
                    help="re-enrich papers that already have a citation count (counts grow)")
    args = ap.parse_args()

    total_changed = 0
    for slug in _categories(args.categories):
        paper_ids = storage.list_paper_ids(slug)
        records, kept_ids = [], []
        for paper_id in paper_ids:
            record = storage.read_paper_json(slug, paper_id)
            if record is None:
                continue
            metadata = record.get("metadata") or {}
            if not args.force and metadata.get("citation_count") is not None:
                continue
            if not s2_id_for(metadata):
                continue  # no arXiv id or DOI to look up
            records.append(record)
            kept_ids.append(paper_id)

        if not records:
            print(f"{slug}: nothing to enrich ({len(paper_ids)} papers)")
            continue

        before = [dict(r.get("metadata") or {}) for r in records]
        changed = enrich_records(records)
        for paper_id, old, record in zip(kept_ids, before, records):
            new = record.get("metadata") or {}
            if new.get("enriched_at") == old.get("enriched_at"):
                continue
            print(f"  {paper_id}: venue={new.get('venue')!r} "
                  f"acronyms={new.get('venue_acronyms')} citations={new.get('citation_count')}")
            if not args.dry_run:
                storage.write_paper_json(slug, record)
        verb = "would enrich" if args.dry_run else "enriched"
        print(f"{slug}: {verb} {changed}/{len(records)} papers")
        total_changed += changed

    print(f"\ntotal: {total_changed} papers {'would be ' if args.dry_run else ''}enriched")
    if not args.dry_run and total_changed:
        print("re-rank affected gaps to pick up the new scores "
              "(ranking_agent.agent.rescore_impact)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
