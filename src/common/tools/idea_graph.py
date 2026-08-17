"""The idea graph as a read-time view over `ideas/`.

There is no graph file. Edges live on each child node, so a write is always one file and
two concurrent sessions never contend — but that makes "children of X" awkward to ask
directly. This module scans the folder once and answers those questions.

Cheap at the scale we have (a few hundred ideas, one JSON each); if it ever isn't, cache
the index rather than introducing a central graph file.

    graph = IdeaGraph.load("transformers")
    graph.for_gap("gap_0963b7ff")      # ideas addressing that gap
    graph.children_of("idea_4f2a11c0") # ideas born from that idea
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.common.schemas.idea import Idea
from src.common.utils import storage


@dataclass
class IdeaGraph:
    ideas: dict[str, Idea] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)  # files that didn't validate

    @classmethod
    def load(cls, category_slug: str) -> "IdeaGraph":
        """Read every committed idea in the category."""
        graph = cls()
        for idea_id in storage.list_idea_ids(category_slug):
            raw = storage.read_idea_json(category_slug, idea_id)
            if raw is None:
                continue
            try:
                graph.ideas[idea_id] = Idea.model_validate(raw)
            except Exception:  # noqa: BLE001 — a stale file shouldn't break the graph
                graph.skipped.append(idea_id)
        return graph

    def get(self, idea_id: str) -> Idea | None:
        return self.ideas.get(idea_id)

    def children_of(self, node_id: str) -> list[Idea]:
        """Ideas with an edge pointing at `node_id` — works for a gap id or an idea id."""
        return [i for i in self.ideas.values()
                if any(p.parent_id == node_id for p in i.parents)]

    def for_gap(self, gap_id: str) -> list[Idea]:
        """Ideas born directly from this gap."""
        return [i for i in self.ideas.values()
                if any(p.parent_id == gap_id and p.parent_kind == "gap" for p in i.parents)]

    def ancestors(self, idea_id: str) -> list[Idea]:
        """The idea's parent chain, nearest first. Stops at gaps (which aren't ideas)."""
        chain: list[Idea] = []
        seen = {idea_id}
        current = self.get(idea_id)
        while current:
            parent = next((p for p in current.parents if p.parent_kind == "idea"), None)
            if parent is None or parent.parent_id in seen:
                break
            seen.add(parent.parent_id)
            current = self.get(parent.parent_id)
            if current is None:
                break
            chain.append(current)
        return chain

    def roots(self) -> list[Idea]:
        """Ideas that came straight from a gap rather than from another idea."""
        return [i for i in self.ideas.values()
                if not any(p.parent_kind == "idea" for p in i.parents)]
