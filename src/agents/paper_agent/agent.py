"""PaperAgent — the top-level LangGraph that runs one paper through the pipeline.

Wires the stages in sequence:

    START → fetch → [ambiguous? → END] → extract → summarize → gap → assemble → END

- `fetch` / `extract` are deterministic common tools (shared with other agents).
- `summarize` / `gap` ARE their subgraphs, mounted directly as nodes (each
  owns its own bounded extractor↔critic cycle — see `subagents/summarize/agent.py`
  and `subagents/gap/agent.py`). Because they're real subgraph nodes,
  `get_graph(xray=True).draw_mermaid()` expands them inline inside this graph's
  diagram, and with `stream(stream_mode=[...], subgraphs=True)` every internal
  extractor/critic step and LLM call streams through nested under this graph
  (see `src/common/utils/logging/trace.py` and `src/common/utils/logging/runlog.py`).

  The subgraphs share state keys with `PaperState`: the inputs
  (`category_slug`/`paper_id`/`force`) flow in, and each writes back exactly one
  output key — `summary_result` / `gaps_result`. Their loop-internal keys are
  private to each subgraph and never touch this state (see their `state.py`).

`run(category_slug, query, force=False)` streams the graph through a
`GraphRunLog` (the shared stream→record→file engine), writing the same per-run
`.jsonl` the dev harness/app produce and echoing progress to the terminal, then
returns the result dict. The dev UI and app backend stream the same graph the
same way to render progress live — one logging implementation for all three.
"""
from __future__ import annotations

import sys
from pathlib import Path

from langgraph.graph import END, START, StateGraph

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.common.tools.paper_extract import extract
from src.common.tools.paper_fetch import PaperAlreadyExistsError, fetch
from src.common.utils import storage
from src.common.utils.logging.runlog import GraphRunLog
from src.agents.paper_agent.state import PaperState
from src.agents.paper_agent.subagents.summarize.agent import build_summarize_graph
from src.agents.paper_agent.subagents.gap.agent import build_gap_graph

# Worst-case super-steps across the whole pipeline: the summarize and gap
# subgraphs each run a bounded extractor↔critic cycle (~2*MAX_LOOPS+2 steps),
# plus this graph's own fetch/extract/assemble. The subgraph nodes inherit this
# graph's run config, so the limit has to cover the sum, with headroom.
_RECURSION_LIMIT = 1 #60


_SOURCE_UNAVAILABLE_WARNING = (
    "source_unavailable: full text could not be downloaded (restricted or "
    "paywalled) — upload a PDF or LaTeX source (.zip/.tar.gz) to continue"
)


def _source_missing(category_slug: str, paper_id: str) -> tuple[bool, dict | None]:
    """Is the stored paper resolved but without any usable full-text source?

    A paper whose fetch only recovered metadata (no pdf, no main .tex) cannot be
    extracted, so the pipeline can't proceed until the user supplies a source.
    Already-extracted papers are never "missing" (a resume that already ran).
    """
    rec = storage.read_paper_json(category_slug, paper_id)
    if rec is None:
        return False, None
    if (rec.get("processing") or {}).get("extracted"):
        return False, rec
    files = ((rec.get("source") or {}).get("files")) or {}
    return not (files.get("pdf") or files.get("tex_main")), rec


def fetch_node(state: PaperState) -> dict:
    """Resolve the query to a stored paper. An ambiguous fetch stores nothing,
    so nothing downstream can run — short-circuit with the fetch result as the
    run's output. A paper that resolved but has no downloadable full text is
    flagged ``needs_source`` and likewise short-circuits, waiting for the user to
    drop in a pdf/tex source. A duplicate continues the pipeline on the existing
    paper (extract/summarize/gap are idempotent)."""
    category_slug, query = state["category_slug"], state["query"]
    try:
        fetch_result = fetch(category_slug, query)
        paper_id = fetch_result["paper"]["paper_id"] if fetch_result["status"] != "ambiguous" else None
    except PaperAlreadyExistsError as e:
        fetch_result = {"status": "duplicate", "paper_id": e.paper_id}
        paper_id = e.paper_id

    if fetch_result["status"] == "ambiguous":
        return {"fetch_result": fetch_result, "result": fetch_result, "ambiguous": True}

    missing, rec = _source_missing(category_slug, paper_id)
    if missing:
        if rec is not None and _SOURCE_UNAVAILABLE_WARNING not in (rec.get("warnings") or []):
            rec.setdefault("warnings", []).append(_SOURCE_UNAVAILABLE_WARNING)
            storage.write_paper_json(category_slug, rec)
        result = {"status": "needs_source", "paper_id": paper_id, "fetch": fetch_result}
        return {"fetch_result": fetch_result, "paper_id": paper_id,
                "needs_source": True, "result": result}

    return {"fetch_result": fetch_result, "paper_id": paper_id}


def extract_node(state: PaperState) -> dict:
    extract_result = extract(state["category_slug"], state["paper_id"],
                             force=state.get("force", False))
    return {"extract_result": extract_result}


def assemble(state: PaperState) -> dict:
    return {"result": {
        "status": "ok",
        "paper_id": state["paper_id"],
        "fetch": state["fetch_result"],
        "extract": state["extract_result"],
        "summary": state["summary_result"],
        "gaps": state["gaps_result"],
    }}


def _route_after_fetch(state: PaperState) -> str:
    return END if state.get("ambiguous") or state.get("needs_source") else "extract"


def build_paper_graph():
    """Compile the paper_agent pipeline graph."""
    g = StateGraph(PaperState)
    g.add_node("fetch", fetch_node)
    g.add_node("extract", extract_node)
    # The subgraphs are mounted directly as nodes (not function wrappers), so
    # they're first-class subgraphs: xray expands them in the diagram and they
    # exchange state with the parent over the shared keys (see their state.py).
    g.add_node("summarize", build_summarize_graph())
    g.add_node("gap", build_gap_graph())
    g.add_node("assemble", assemble)

    g.add_edge(START, "fetch")
    g.add_conditional_edges("fetch", _route_after_fetch, {"extract": "extract", END: END})
    g.add_edge("extract", "summarize")
    g.add_edge("summarize", "gap")
    g.add_edge("gap", "assemble")
    g.add_edge("assemble", END)
    return g.compile()


_GRAPH = None


def _get_graph():
    global _GRAPH
    if _GRAPH is None:
        _GRAPH = build_paper_graph()
    return _GRAPH


def run(category_slug: str, query: str, force: bool = False, *,
        stream: bool = True, echo: bool | None = None) -> dict:
    """Fetch a paper into ``category_slug`` and run it through extract,
    summarize+critique, and gap extraction+critique. Returns the fetch result
    as-is if the fetch was ambiguous (no paper stored). Standalone-callable
    (the dev harness and eval pipeline import it directly).

    By default (``stream=True``) the agent owns its own logging: it streams the
    graph through a :class:`~src.common.utils.logging.runlog.GraphRunLog`, writing the
    *same* per-run ``.jsonl`` the dev harness/app produce to
    ``workspace/<slug>/logs/paper_agent/``, and echoes progress to the terminal
    when attached to a TTY (override with ``echo=True/False``). Pass
    ``stream=False`` for a quiet, log-free ``.invoke()`` (used by the eval
    pipeline, which does its own reporting)."""
    graph = _get_graph()
    inputs = {"category_slug": category_slug, "query": query, "force": force}
    if not stream:
        return graph.invoke(inputs, config={"recursion_limit": _RECURSION_LIMIT})["result"]

    if echo is None:
        echo = sys.stderr.isatty()
    rl = GraphRunLog("paper_agent", workspace=category_slug, run_hint=query,
                     recursion_limit=_RECURSION_LIMIT)
    rl.run(graph, inputs, echo=echo)
    if rl.error:
        raise RuntimeError(rl.error)
    return rl.output


if __name__ == "__main__":
    import json

    category = sys.argv[1] if len(sys.argv) > 1 else "transformers"
    query = sys.argv[2] if len(sys.argv) > 2 else "1706.03762"
    print(json.dumps(run(category, query), indent=2, default=str))
