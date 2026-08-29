"""Category agent — bootstrap a workspace from just a topic name.

The pipeline's entry point: everything downstream (paper, director, validator,
ranking) keys off a category *slug* and refuses to run until that category has a
``meta.json`` (see ``storage.ensure_category``). Nothing else creates it — this
agent does. Given a topic like ``"Machine Learning"`` it:

1. **describe** — asks the LLM (the fast ``cheap`` role, no reasoning model) for
   a corrected spelling of the topic name plus a short, technical description of
   it as a research category. The *slug* (and thus the workspace folder) is
   still derived from the raw input topic, so a typo doesn't change which
   folder gets written to — only the displayed ``name`` is corrected.
2. **write_category** — slugifies the topic and writes ``workspace/<slug>/meta.json``,
   the record the rest of the pipeline depends on. Re-running an existing category
   refreshes name/description while preserving ``created_at``.

Built as a tiny LangGraph (``describe → write_category``) so it streams and logs
through the shared ``GraphRunLog`` like every other agent.

The description written here is only a starting point — the app UI lets the user
edit it by hand afterwards (``PATCH /api/topics/{slug}``).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.common.utils import storage
from src.common.utils.logging.runlog import GraphRunLog
from src.common.utils.logging.trace import log_event
from src.agents.category_agent.state import CategoryState
from src.llm.registry import chat_with_structured_output, get_llm

_SYSTEM_PROMPT = (
    "You name and describe research categories for a research ideation tool. "
    "Given a topic name (which may be misspelled or inconsistently capitalized), "
    "return the corrected, canonically-spelled and capitalized name of the field "
    "(e.g. 'mashine lerning' -> 'Machine Learning'; if it's already correct, "
    "return it unchanged), plus a 2-3 sentence technical description aimed at a "
    "researcher: what the field/topic covers, its core techniques or objects of "
    "study, and how it relates to neighboring areas. Be precise and technical, "
    "not promotional."
)


class CategoryProfile(BaseModel):
    name: str = Field(description="The corrected, canonically-spelled category name.")
    description: str = Field(description="A 2-3 sentence technical description.")


def _slugify(value: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "_" for ch in value.strip())
    while "__" in cleaned:
        cleaned = cleaned.replace("__", "_")
    return cleaned.strip("_") or "topic"


# --- graph nodes -------------------------------------------------------------

def describe(state: CategoryState) -> dict:
    """Ask the LLM for a corrected name and a short technical description of
    the topic. The slug is derived from the raw input, so a spelling fix never
    moves the workspace to a different folder."""
    topic = (state.get("topic") or "").strip()
    if not topic:
        raise ValueError("Topic must not be empty.")

    slug = _slugify(topic)
    llm = get_llm("cheap")
    profile = chat_with_structured_output(
        llm, _SYSTEM_PROMPT, f"Topic: {topic}", CategoryProfile
    )
    name = (profile.name or topic).strip() or topic
    description = (profile.description or "").strip()
    log_event(f"described category '{topic}'", corrected_name=name, chars=len(description))

    return {
        "topic": topic,
        "category_slug": slug,
        "name": name,
        "description": description,
    }


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
        "meta_file": str(meta_path),
    }}


def build_category_graph():
    """Compile the graph: describe → write_category."""
    g = StateGraph(CategoryState)
    g.add_node("describe", describe)
    g.add_node("write_category", write_category)

    g.add_edge(START, "describe")
    g.add_edge("describe", "write_category")
    g.add_edge("write_category", END)
    return g.compile()


_GRAPH = None


def _get_graph():
    global _GRAPH
    if _GRAPH is None:
        _GRAPH = build_category_graph()
    return _GRAPH


def run(topic: str, *, stream: bool = True, echo: bool | None = None) -> dict:
    """Bootstrap the research category named by ``topic``: ask the LLM for a
    short technical description and write ``meta.json`` into a fresh
    ``workspace/<slug>/``. Returns a summary dict. Standalone-callable.

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
