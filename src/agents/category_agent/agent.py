"""Category agent — bootstrap a workspace from a short Wikipedia description.

The pipeline's entry point: everything downstream (paper, director, validator,
ranking) keys off a category *slug* and refuses to run until that category has a
``meta.json`` (see ``storage.ensure_category``). Nothing else creates it — this
agent does. Given a topic like ``"Machine Learning"`` it:

1. **fetch**  — looks the topic up on Wikipedia and takes the short lead blurb as
   the category description (reusing the fetch helpers from ``old_agent``). No LLM.
2. **write_category** — slugifies the topic and writes ``workspace/<slug>/meta.json``,
   the record the rest of the pipeline depends on. Re-running an existing category
   refreshes name/description while preserving ``created_at``.

Built as a tiny LangGraph (``fetch → write_category``) so it streams and logs
through the shared ``GraphRunLog`` like every other agent. If Wikipedia has no
page for the topic, the workspace is still created (with an empty description and
a warning) so topic creation never hard-fails on a lookup miss.

The richer LLM variant — name + description + a few seminal papers — is archived
under ``archive/`` for later use.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from langgraph.graph import END, START, StateGraph

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.common.utils import storage
from src.common.utils.logging.runlog import GraphRunLog
from src.common.utils.logging.trace import log_event
from src.agents.category_agent.state import CategoryState

# Reuse the legacy Wikipedia fetchers rather than duplicating them.
from src.agents.category_agent.old_agent import _fetch_page_summary, _slugify

# The Wikipedia lead is usually short already; cap it so a long lead still yields
# a *short* description, trimmed at a sentence boundary where possible.
_MAX_DESCRIPTION_CHARS = 600


def _shorten(text: str, max_chars: int = _MAX_DESCRIPTION_CHARS) -> str:
    """A short blurb: the text as-is if already short, else cut at the last
    sentence end before ``max_chars`` (falling back to a hard cut + ellipsis)."""
    text = (text or "").strip()
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    if end >= max_chars // 2:
        return cut[: end + 1].strip()
    return cut.rstrip() + "…"


# --- graph nodes -------------------------------------------------------------

def fetch(state: CategoryState) -> dict:
    """Look the topic up on Wikipedia and take a short description. Best-effort:
    if there's no page, proceed with an empty description + a warning so the
    workspace is still created."""
    topic = (state.get("topic") or "").strip()
    if not topic:
        raise ValueError("Topic must not be empty.")

    slug = _slugify(topic)
    out: dict = {"topic": topic, "category_slug": slug}
    try:
        page = _fetch_page_summary(topic)
        # ``summary`` is the lead paragraph (a few sentences); ``description`` is
        # Wikipedia's one-line gloss. Prefer the lead, trimmed to a short blurb.
        description = _shorten(page.summary) or page.description
        out["name"] = page.title or topic
        out["description"] = description
        out["source_url"] = page.url
        log_event(f"fetched Wikipedia description for '{topic}'",
                  chars=len(description), page=page.title)
    except Exception as exc:  # noqa: BLE001 — a lookup miss must not fail creation
        out["name"] = topic
        out["description"] = ""
        out["warning"] = f"no Wikipedia page found: {exc}"
        log_event(f"no Wikipedia page for '{topic}', creating with empty description")
    return out


def write_category(state: CategoryState) -> dict:
    """Write ``workspace/<slug>/meta.json`` — the record the rest of the pipeline
    requires. Preserves ``created_at`` when refreshing an existing category."""
    slug = state["category_slug"]
    meta_path = storage.category_dir(slug) / "meta.json"

    created_at = storage.now_iso()
    if meta_path.exists():
        try:
            existing = json.loads(meta_path.read_text(encoding="utf-8"))
            created_at = existing.get("created_at", created_at)
        except (json.JSONDecodeError, OSError):
            pass

    now = storage.now_iso()
    meta = {
        "category_slug": slug,
        "name": state.get("name") or slug,
        "description": state.get("description") or "",
        "source_url": state.get("source_url") or None,
        "created_at": created_at,
        "updated_at": now,
    }
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(
        json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    log_event(f"wrote category '{slug}'", path=str(meta_path))

    return {"result": {
        "status": "ok",
        "category_slug": slug,
        "name": meta["name"],
        "description": meta["description"],
        "source_url": meta["source_url"],
        "warning": state.get("warning"),
        "meta_file": str(meta_path),
    }}


def build_category_graph():
    """Compile the graph: fetch → write_category."""
    g = StateGraph(CategoryState)
    g.add_node("fetch", fetch)
    g.add_node("write_category", write_category)

    g.add_edge(START, "fetch")
    g.add_edge("fetch", "write_category")
    g.add_edge("write_category", END)
    return g.compile()


_GRAPH = None


def _get_graph():
    global _GRAPH
    if _GRAPH is None:
        _GRAPH = build_category_graph()
    return _GRAPH


def run(topic: str, *, stream: bool = True, echo: bool | None = None) -> dict:
    """Bootstrap the research category named by ``topic``: take a short Wikipedia
    description and write ``meta.json`` into a fresh ``workspace/<slug>/``. Returns
    a summary dict. Standalone-callable.

    By default streams the graph through a :class:`GraphRunLog`, writing the
    standard per-run ``.jsonl`` to ``workspace/<slug>/logs/category_agent/`` and
    echoing to the terminal on a TTY. Pass ``stream=False`` for a quiet
    ``.invoke()``.
    """
    topic = (topic or "").strip()
    if not topic:
        raise ValueError("Topic must not be empty.")

    graph = _get_graph()
    inputs = {"topic": topic}
    if not stream:
        return graph.invoke(inputs)["result"]

    if echo is None:
        echo = sys.stderr.isatty()
    # The workspace is named by the slug this agent is about to create.
    slug = _slugify(topic)
    rl = GraphRunLog("category_agent", workspace=slug, run_hint=topic)
    rl.run(graph, inputs, echo=echo)
    if rl.error:
        raise RuntimeError(rl.error)
    return rl.output


if __name__ == "__main__":
    topic_value = " ".join(sys.argv[1:]) or "Machine Learning"
    print(json.dumps(run(topic_value), indent=2, ensure_ascii=False))
