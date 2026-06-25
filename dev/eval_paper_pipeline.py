"""Eval harness for the summary and gap-extraction stages.

Re-runs summarize()/extract_gaps() (force=True) over a set of papers in a
category — each already runs its own bounded extractor<->critic loop
internally, not just a single extractor pass — times each call, and writes
a human-readable report plus raw JSON. The raw JSON is the diff target for
future prompt/model variants — run this script again after a change, label
it differently, and compare.

Usage:
    # Evaluate every already-extracted paper in the workspace.
    python -m dev.eval_paper_pipeline --category transformers --label baseline

    # Fetch+extract specific papers by name/arXiv-ID/URL/DOI (idempotent —
    # rerunning reuses the cached fetch+extract), then evaluate just those.
    python -m dev.eval_paper_pipeline --category transformers --label baseline \
        --mode summary --queries "Attention Is All You Need" 1406.2661

    # Evaluate specific existing paper_ids without touching fetch/extract.
    python -m dev.eval_paper_pipeline --category transformers --label baseline \
        --mode gap --papers arxiv_1706.03762

Also importable from dev/ui.py to drive the same pipelines from the web UI.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import _env_bootstrap  # noqa: F401,E402

from src.agents.paper_agent import run as paper_agent_run  # noqa: E402
from src.agents.paper_agent.subagents.gap import extract_gaps  # noqa: E402
from src.agents.paper_agent.subagents.summarize import summarize  # noqa: E402
from src.common.utils import storage  # noqa: E402

EVAL_DIR = Path(__file__).resolve().parent / "eval_runs"

OnProgress = Callable[[dict], None] | None


def ensure_papers_fetched(
    category_slug: str, queries: list[str], on_progress: OnProgress = None
) -> tuple[list[str], list[dict]]:
    """Resolve a list of paper names/arXiv-IDs/URLs/DOIs into ``paper_id``s.

    Runs the full paper_agent pipeline (fetch -> extract -> summarize ->
    verify -> gaps) with ``force=False`` for each query, so a query already
    present in the workspace is a no-op (cached) rather than re-fetched.

    Returns ``(paper_ids, issues)`` where ``issues`` lists queries that
    couldn't be resolved (e.g. ambiguous title matches).
    """
    storage.ensure_category(category_slug)
    paper_ids: list[str] = []
    issues: list[dict] = []
    total = len(queries)
    for i, query in enumerate(queries):
        if on_progress:
            on_progress({"phase": "fetch", "query": query, "index": i, "total": total,
                          "status": "running"})
        result = paper_agent_run(category_slug, query, force=False)
        if result.get("status") == "ambiguous":
            issues.append({"query": query, "candidates": result.get("candidates")})
            if on_progress:
                on_progress({"phase": "fetch", "query": query, "index": i, "total": total,
                              "status": "ambiguous"})
            continue
        paper_ids.append(result["paper_id"])
        if on_progress:
            on_progress({"phase": "fetch", "query": query, "index": i, "total": total,
                          "status": "done", "paper_id": result["paper_id"]})
    return paper_ids, issues


def _fully_extracted_papers(category_slug: str) -> list[str]:
    pdir = storage.category_dir(category_slug) / "papers"
    if not pdir.exists():
        return []
    out = []
    for d in sorted(pdir.iterdir()):
        rec = storage.read_paper_json(category_slug, d.name)
        if rec and rec.get("processing", {}).get("extracted"):
            out.append(d.name)
    return out


def list_workspaces() -> list[dict]:
    """All categories under the workspace root, with their extracted-paper count."""
    root = storage.workspace_root()
    if not root.exists():
        return []
    out = []
    for d in sorted(root.iterdir()):
        if not d.is_dir() or not (d / "meta.json").exists():
            continue
        n = len(_fully_extracted_papers(d.name))
        out.append({"category_slug": d.name, "n_extracted_papers": n})
    return out


def _read_gap_records(category_slug: str, gap_ids: list[str]) -> list[dict]:
    pending = storage.category_dir(category_slug) / "gaps" / "pending"
    records = []
    for gid in gap_ids:
        path = pending / f"{gid}.json"
        if path.exists():
            records.append(json.loads(path.read_text()))
    return records


def _title_of(rec: dict, paper_id: str) -> str:
    return (rec.get("metadata") or {}).get("title") or rec.get("title") or paper_id


def run_one_summary(category_slug: str, paper_id: str) -> dict:
    rec = storage.read_paper_json(category_slug, paper_id)
    title = _title_of(rec, paper_id)

    t0 = time.time()
    summary_result = summarize(category_slug, paper_id, force=True)
    summary_dur = time.time() - t0

    rec = storage.read_paper_json(category_slug, paper_id)  # re-read post-write
    summary = rec.get("summary") or {}

    return {
        "paper_id": paper_id,
        "title": title,
        "timing": {"summary_s": round(summary_dur, 2)},
        "summary_result": summary_result,
        "verify_result": summary_result.get("verification"),
        "summary": summary,
    }


def run_one_gap(category_slug: str, paper_id: str) -> dict:
    rec = storage.read_paper_json(category_slug, paper_id)
    title = _title_of(rec, paper_id)

    if not (rec.get("processing") or {}).get("summarized"):
        summarize(category_slug, paper_id, force=False)

    t0 = time.time()
    gaps_result = extract_gaps(category_slug, paper_id, force=True)
    gaps_dur = time.time() - t0

    rec = storage.read_paper_json(category_slug, paper_id)  # re-read post-write
    gap_ids = gaps_result.get("gap_ids") or []
    gap_records = _read_gap_records(category_slug, gap_ids)

    n_ungrounded = sum(
        1 for g in gap_records
        for ev in g.get("evidence", [])
        if ev.get("char_offset") is None
    )

    return {
        "paper_id": paper_id,
        "title": title,
        "timing": {"gaps_s": round(gaps_dur, 2)},
        "gaps_result_meta": gaps_result,
        "gaps": gap_records,
        "n_gaps": len(gap_records),
        "n_ungrounded_quotes": n_ungrounded,
    }


def run_one_both(category_slug: str, paper_id: str) -> dict:
    """Summary + gaps in one pass (original combined eval)."""
    s = run_one_summary(category_slug, paper_id)
    g = run_one_gap(category_slug, paper_id)
    merged = {**s, **g}
    merged["timing"] = {**s["timing"], **g["timing"]}
    return merged


RUN_ONE = {"summary": run_one_summary, "gap": run_one_gap, "both": run_one_both}


def run_pipeline(
    category_slug: str,
    mode: str,
    paper_ids: list[str] | None = None,
    on_progress: OnProgress = None,
) -> list[dict]:
    """Run ``mode`` ('summary' | 'gap' | 'both') over a list of papers.

    Defaults to every fully-extracted paper in the category. Calls
    ``on_progress({"paper_id", "index", "total", "status"})`` before/after
    each paper if given, for live UI progress.
    """
    if mode not in RUN_ONE:
        raise ValueError(f"mode must be one of {sorted(RUN_ONE)}")
    run_one = RUN_ONE[mode]

    ids = paper_ids or _fully_extracted_papers(category_slug)
    if not ids:
        raise ValueError(f"No extracted papers found in category '{category_slug}'")

    results = []
    total = len(ids)
    for i, pid in enumerate(ids):
        if on_progress:
            on_progress({"phase": "eval", "paper_id": pid, "index": i, "total": total,
                          "status": "running"})
        results.append(run_one(category_slug, pid))
        if on_progress:
            on_progress({"phase": "eval", "paper_id": pid, "index": i, "total": total,
                          "status": "done"})
    return results


def _fmt_list(items: list[str], indent: str = "  ") -> str:
    if not items:
        return f"{indent}(none)\n"
    return "".join(f"{indent}- {x}\n" for x in items)


def render_markdown_summary(label: str, category_slug: str, results: list[dict]) -> str:
    lines = [f"# Summary-agent eval: {label}", f"Category: `{category_slug}`",
              f"Generated: {datetime.now().isoformat(timespec='seconds')}", ""]

    total_s = sum(r["timing"]["summary_s"] for r in results)
    lines.append("## Summary")
    lines.append(f"- Papers: {len(results)}")
    lines.append(f"- Total summarize() time: {total_s:.1f}s "
                  f"(avg {total_s / max(len(results), 1):.1f}s/paper)")
    lines.append("")
    lines.append("| paper_id | summary_s |")
    lines.append("|---|---|")
    for r in results:
        lines.append(f"| {r['paper_id']} | {r['timing']['summary_s']} |")
    lines.append("")
    lines.append("---")

    for r in results:
        s = r["summary"]
        lines.append(f"\n## {r['paper_id']} — {r['title']}")
        lines.append(f"_summary: {r['timing']['summary_s']}s_")
        lines.append(f"- paper_type: `{s.get('paper_type')}`")
        lines.append(f"- keywords: {', '.join(s.get('keywords') or [])}")
        lines.append(f"- problem: {s.get('problem', {}).get('statement', '')}")
        lines.append(f"- approach: {s.get('approach', {}).get('summary', '')}")
        lines.append("- contributions:")
        lines.append(_fmt_list(
            [c.get("description", "") for c in (s.get("contributions") or [])]))
        lines.append("- limitations_stated:")
        lines.append(_fmt_list(s.get("limitations_stated") or []))
        lines.append("- open_threads:")
        lines.append(_fmt_list(s.get("open_threads") or []))
        lines.append(f"- context_summary: {s.get('context_summary', '')}")
        lines.append("")

    return "\n".join(lines)


def render_markdown_gap(label: str, category_slug: str, results: list[dict]) -> str:
    lines = [f"# Gap-agent eval: {label}", f"Category: `{category_slug}`",
              f"Generated: {datetime.now().isoformat(timespec='seconds')}", ""]

    total_gaps_s = sum(r["timing"]["gaps_s"] for r in results)
    total_gaps_n = sum(r["n_gaps"] for r in results)
    total_ungrounded = sum(r["n_ungrounded_quotes"] for r in results)

    lines.append("## Summary")
    lines.append(f"- Papers: {len(results)}")
    lines.append(f"- Total gap stage time: {total_gaps_s:.1f}s "
                  f"(avg {total_gaps_s / max(len(results), 1):.1f}s/paper)")
    lines.append(f"- Total gaps extracted: {total_gaps_n}")
    lines.append(f"- Ungrounded quotes: {total_ungrounded}")
    lines.append("")
    lines.append("| paper_id | gaps_s | n_gaps | ungrounded |")
    lines.append("|---|---|---|---|")
    for r in results:
        lines.append(f"| {r['paper_id']} | {r['timing']['gaps_s']} | "
                      f"{r['n_gaps']} | {r['n_ungrounded_quotes']} |")
    lines.append("")
    lines.append("---")

    for r in results:
        lines.append(f"\n## {r['paper_id']} — {r['title']}")
        lines.append(f"_gaps: {r['timing']['gaps_s']}s · "
                      f"{r['n_gaps']} gaps · {r['n_ungrounded_quotes']} ungrounded_")
        if not r["gaps"]:
            lines.append("(none)")
        for g in r["gaps"]:
            ev = (g.get("evidence") or [{}])[0]
            grounded = "✓" if ev.get("char_offset") is not None else "✗ UNGROUNDED"
            lines.append(f"- **[{g.get('type')}]** {g.get('title')} ({grounded})")
            lines.append(f"  - description: {g.get('description')}")
            lines.append(f"  - canonical: `{g.get('canonical_description')}`")
            lines.append(f"  - quote ({ev.get('section')}): \"{ev.get('quote')}\"")
        lines.append("")

    return "\n".join(lines)


def render_markdown_both(label: str, category_slug: str, results: list[dict]) -> str:
    lines = [f"# Eval run: {label}", f"Category: `{category_slug}`",
              f"Generated: {datetime.now().isoformat(timespec='seconds')}", ""]

    total_summary_s = sum(r["timing"]["summary_s"] for r in results)
    total_gaps_s = sum(r["timing"]["gaps_s"] for r in results)
    total_gaps_n = sum(r["n_gaps"] for r in results)
    total_ungrounded = sum(r["n_ungrounded_quotes"] for r in results)

    lines.append("## Summary")
    lines.append(f"- Papers: {len(results)}")
    lines.append(f"- Total summarize() time: {total_summary_s:.1f}s "
                  f"(avg {total_summary_s / max(len(results), 1):.1f}s/paper)")
    lines.append(f"- Total gap stage time: {total_gaps_s:.1f}s "
                  f"(avg {total_gaps_s / max(len(results), 1):.1f}s/paper)")
    lines.append(f"- Total gaps extracted: {total_gaps_n}")
    lines.append(f"- Ungrounded quotes: {total_ungrounded}")
    lines.append("")
    lines.append("| paper_id | summary_s | gaps_s | n_gaps | ungrounded |")
    lines.append("|---|---|---|---|---|")
    for r in results:
        lines.append(f"| {r['paper_id']} | {r['timing']['summary_s']} | "
                      f"{r['timing']['gaps_s']} | {r['n_gaps']} | "
                      f"{r['n_ungrounded_quotes']} |")
    lines.append("")
    lines.append("---")

    for r in results:
        s = r["summary"]
        lines.append(f"\n## {r['paper_id']} — {r['title']}")
        lines.append(f"_summary: {r['timing']['summary_s']}s · "
                      f"gaps: {r['timing']['gaps_s']}s · "
                      f"{r['n_gaps']} gaps · "
                      f"{r['n_ungrounded_quotes']} ungrounded_")

        lines.append("\n**Summary**")
        lines.append(f"- paper_type: `{s.get('paper_type')}`")
        lines.append(f"- keywords: {', '.join(s.get('keywords') or [])}")
        lines.append(f"- problem: {s.get('problem', {}).get('statement', '')}")
        lines.append(f"- approach: {s.get('approach', {}).get('summary', '')}")
        lines.append("- contributions:")
        lines.append(_fmt_list(
            [c.get("description", "") for c in (s.get("contributions") or [])]))
        lines.append("- limitations_stated:")
        lines.append(_fmt_list(s.get("limitations_stated") or []))
        lines.append("- open_threads:")
        lines.append(_fmt_list(s.get("open_threads") or []))
        lines.append(f"- context_summary: {s.get('context_summary', '')}")

        lines.append("\n**Gaps**")
        if not r["gaps"]:
            lines.append("(none)")
        for g in r["gaps"]:
            ev = (g.get("evidence") or [{}])[0]
            grounded = "✓" if ev.get("char_offset") is not None else "✗ UNGROUNDED"
            lines.append(f"- **[{g.get('type')}]** {g.get('title')} ({grounded})")
            lines.append(f"  - description: {g.get('description')}")
            lines.append(f"  - canonical: `{g.get('canonical_description')}`")
            lines.append(f"  - quote ({ev.get('section')}): \"{ev.get('quote')}\"")
        lines.append("")

    return "\n".join(lines)


RENDER_MARKDOWN = {
    "summary": render_markdown_summary,
    "gap": render_markdown_gap,
    "both": render_markdown_both,
}


def write_report(label: str, category_slug: str, mode: str, results: list[dict]) -> Path:
    """Write report.md + raw.json under EVAL_DIR/<timestamp>_<label>/ and return the dir."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = EVAL_DIR / f"{ts}_{mode}_{label}"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "raw.json").write_text(json.dumps(results, indent=2, default=str))
    md = RENDER_MARKDOWN[mode](label, category_slug, results)
    (run_dir / "report.md").write_text(md)
    return run_dir


def _progress_printer(event: dict) -> None:
    if event["phase"] == "fetch":
        label = event.get("query")
        extra = f" -> {event['paper_id']}" if event["status"] == "done" else ""
    else:
        label = event.get("paper_id")
        extra = ""
    print(f"  [{event['phase']}][{event['index'] + 1}/{event['total']}] "
          f"{label} ({event['status']}){extra}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--category", required=True, help="workspace/category name")
    ap.add_argument("--label", required=True, help="run label, e.g. 'baseline'")
    ap.add_argument("--mode", choices=["summary", "gap", "both"], default="both")
    ap.add_argument("--queries", nargs="*", default=None,
                     help="paper names/arXiv-IDs/URLs/DOIs to fetch+extract into the "
                          "workspace (idempotent — rerunning reuses already fetched & "
                          "extracted papers) before evaluating them")
    ap.add_argument("--papers", nargs="*", default=None,
                     help="specific existing paper_ids to evaluate (skips fetch/extract); "
                          "default with no --queries = all extracted papers in category")
    args = ap.parse_args()

    if args.queries:
        print(f"Ensuring {len(args.queries)} paper(s) are fetched+extracted "
              f"in '{args.category}'...")
        paper_ids, issues = ensure_papers_fetched(
            args.category, args.queries, on_progress=_progress_printer
        )
        for issue in issues:
            print(f"  ! could not resolve '{issue['query']}': {issue['candidates']}")
    else:
        paper_ids = args.papers

    print(f"\nRunning eval '{args.label}' (mode={args.mode}) on category '{args.category}'")
    results = run_pipeline(args.category, args.mode, paper_ids, on_progress=_progress_printer)

    run_dir = write_report(args.label, args.category, args.mode, results)
    print(f"\nWrote report: {run_dir / 'report.md'}")
    print(f"Wrote raw:    {run_dir / 'raw.json'}")


if __name__ == "__main__":
    main()
