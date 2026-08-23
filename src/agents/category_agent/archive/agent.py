"""Category agent — bootstrap a research-category workspace from a free-text topic.

The pipeline's entry point: everything downstream (paper, director, validator,
ranking) keys off a category *slug* and refuses to run until that category has a
``meta.json`` (see ``storage.ensure_category``). Nothing else creates it — this
agent does. Given a topic like ``"Machine Learning"`` it:

1. **research**  — gathers grounding background on the field (Wikipedia summary +
   related pages, reusing the helpers from the legacy ``old_agent``) and saves a
   dossier into the new workspace.
2. **describe**  — an LLM (via ``src/llm/registry``) turns topic + background into
   a human-readable ``name``, a short ``description`` (2–4 sentences), and a few
   seminal papers of the field.
3. **write_category** — slugifies the topic and writes ``workspace/<slug>/meta.json``,
   the record the rest of the pipeline depends on. Re-running an existing category
   refreshes name/description/papers while preserving ``created_at``.

Built as a small LangGraph (``research → describe → write_category → finalize``)
so it streams and logs through the shared ``GraphRunLog`` engine exactly like the
paper and director agents. LLM calls through ``src/llm/registry`` are traced for
free; a deterministic offline mock is used when no API key is set, so dev runs
work without network access.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.common.utils import storage
from src.common.utils.logging.runlog import GraphRunLog
from src.common.utils.logging.trace import log_event
from src.llm.registry import chat_with_structured_output, get_llm
from src.agents.category_agent.archive.state import CategoryState

# Reuse the legacy Wikipedia fetchers rather than duplicating them.
from src.agents.category_agent.old_agent import (
    _fetch_page_summary,
    _fetch_related_titles,
    _render_knowledge_text,
    _slugify,
)


# --- LLM output schema -------------------------------------------------------

class SeminalPaper(BaseModel):
    """One foundational paper of the field."""

    title: str = Field(description="Paper title.")
    authors: str = Field(description="First author et al., or the key authors.")
    year: int | None = Field(default=None, description="Publication year, if known.")
    why: str = Field(description="One line on why it is foundational for this field.")


class CategoryProfile(BaseModel):
    """The category description the agent writes into ``meta.json``."""

    name: str = Field(description="Human-readable category name, e.g. 'Computer Vision'.")
    description: str = Field(
        description=(
            "A short description of the research category — 2 to 4 sentences "
            "covering what the field studies and its main problems."
        )
    )
    seminal_papers: list[SeminalPaper] = Field(
        default_factory=list,
        description="3–6 foundational papers of the field, most influential first.",
    )


_SYSTEM_PROMPT = (
    "You are a research librarian helping bootstrap a new research-category "
    "workspace. Given a topic and background material, write a concise, accurate "
    "profile of the research field: a clean category name, a short description "
    "(2–4 sentences, no marketing fluff), and a few genuinely seminal papers. "
    "Only list papers you are confident actually exist and are foundational; "
    "prefer a shorter, correct list over a padded one."
)


# --- graph nodes -------------------------------------------------------------

def research(state: CategoryState) -> dict:
    """Gather grounding background on the topic and stash a dossier in the new
    workspace. Best-effort: if Wikipedia is unreachable the graph still proceeds
    with an empty background (the LLM then works from the topic alone)."""
    topic = (state.get("topic") or "").strip()
    if not topic:
        raise ValueError("Topic must not be empty.")

    slug = _slugify(topic)
    background = ""
    background_file = ""
    try:
        main_page = _fetch_page_summary(topic)
        related_titles = _fetch_related_titles(main_page.title, limit=6)
        related_pages = []
        for title in related_titles:
            try:
                related_pages.append(_fetch_page_summary(title))
            except Exception:
                continue
        background = _render_knowledge_text(topic, main_page, related_pages)

        # Save the dossier inside the workspace so it stays as grounding context.
        out_path = storage.category_dir(slug) / "research" / f"{slug}_background.md"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(background, encoding="utf-8")
        background_file = str(out_path)
        log_event(f"gathered background for '{topic}'", chars=len(background))
    except Exception as exc:  # noqa: BLE001 — research is best-effort grounding
        log_event(f"background lookup failed, proceeding topic-only: {exc}")

    return {
        "topic": topic,
        "category_slug": slug,
        "background": background,
        "background_file": background_file,
    }


def describe(state: CategoryState) -> dict:
    """Ask the LLM to write the category name, short description, and seminal
    papers, grounded in the research background."""
    topic = state["topic"]
    background = state.get("background") or ""

    user_prompt = (
        f"Topic to describe: {topic}\n\n"
        "Background material (may be partial or empty):\n"
        f"{background[:6000] if background else '(no background available)'}\n\n"
        "Write the category profile."
    )
    profile = chat_with_structured_output(
        get_llm("reasoner"), _SYSTEM_PROMPT, user_prompt, CategoryProfile
    )

    papers = [p.model_dump() for p in profile.seminal_papers]
    log_event(
        f"described '{topic}' as '{profile.name}'",
        description_chars=len(profile.description),
        n_papers=len(papers),
    )
    return {
        "name": profile.name.strip() or topic,
        "description": profile.description.strip(),
        "seminal_papers": papers,
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
        "name": state["name"],
        "description": state["description"],
        "seminal_papers": state.get("seminal_papers") or [],
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
        "name": state["name"],
        "description": state["description"],
        "seminal_papers": meta["seminal_papers"],
        "meta_file": str(meta_path),
        "background_file": state.get("background_file") or None,
    }}


def build_category_graph():
    """Compile the graph: research → describe → write_category → finalize."""
    g = StateGraph(CategoryState)
    g.add_node("research", research)
    g.add_node("describe", describe)
    g.add_node("write_category", write_category)

    g.add_edge(START, "research")
    g.add_edge("research", "describe")
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
    """Bootstrap the research category named by ``topic``: research it, have an
    LLM write a short description + seminal papers, and write ``meta.json`` into a
    fresh ``workspace/<slug>/``. Returns a summary dict. Standalone-callable.

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
