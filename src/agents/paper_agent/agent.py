"""PaperAgent — runs a single paper through the full pipeline.

Wires the stages in sequence:
  1. fetch       (common tool, shared with other agents)
  2. extract      (common tool, shared with other agents)
  3+3b. summarize (subagent — owns its own extractor<->critic loop)
  4+4b. gap       (subagent — owns its own extractor<->critic loop)

Each subagent is a single call here: `summarize()` and `extract_gaps()` are
the only entry points their packages expose, and each runs its own bounded
extractor<->critic loop internally (see subagents/summarize/summarize.py and
subagents/gap/gap.py) — this module doesn't loop at all, it just sequences
the four stages.

Progress/prompts are recorded via ``src.common.utils.runlog`` (``start_run``
+ ``step``) rather than a bespoke callback — every extractor/critic call a
subagent makes nests automatically under that subagent's own step, so a
run's log shows e.g. "summarize" with as many nested "extractor"/"critic"
entries as that run took. See planning/technical-discussions/LOGGING_SYSTEM.md.
"""
from __future__ import annotations

from ...common.tools.paper_extract import extract
from ...common.tools.paper_fetch import PaperAlreadyExistsError, fetch
from ...common.utils.runlog import skip_step, start_run, step
from .subagents.gap import extract_gaps
from .subagents.summarize import summarize

# Ordered pipeline steps, used to skip the rest of the list when fetch comes
# back ambiguous (no paper stored, nothing downstream can run).
STEPS = ["fetch", "extract", "summarize", "gap"]


def run(category_slug: str, query: str, force: bool = False) -> dict:
    """Fetch a paper into ``category_slug`` and run it through extract,
    summarize+critique, and gap extraction+critique.

    Returns the fetch result as-is if the fetch was ambiguous (no paper was
    stored). On a duplicate, logs the duplicate and continues the pipeline on
    the existing paper (extract/summarize/gap are idempotent).
    """
    with start_run(category_slug, "paper_agent",
                    {"category_slug": category_slug, "query": query, "force": force},
                    run_hint=query) as run_log:

        with step("fetch") as node:
            try:
                fetch_result = fetch(category_slug, query)
            except PaperAlreadyExistsError as e:
                paper_id = e.paper_id
                fetch_result = {"status": "duplicate", "paper_id": paper_id}
            else:
                if fetch_result["status"] == "ambiguous":
                    node["data"]["result"] = fetch_result
                    for s in STEPS[1:]:
                        skip_step(s)
                    run_log.output = fetch_result
                    return fetch_result
                paper_id = fetch_result["paper"]["paper_id"]
            node["data"]["result"] = fetch_result

        with step("extract") as node:
            extract_result = extract(category_slug, paper_id, force=force)
            node["data"]["result"] = extract_result

        summary_result = summarize(category_slug, paper_id, force=force)

        gaps_result = extract_gaps(category_slug, paper_id, force=force)

        run_log.output = {
            "status": "ok",
            "paper_id": paper_id,
            "fetch": fetch_result,
            "extract": extract_result,
            "summary": summary_result,
            "gaps": gaps_result,
        }
        return run_log.output
