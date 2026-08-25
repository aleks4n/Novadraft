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

from src.common.schemas.idea import IdeaRecord
from src.common.schemas.idea_compat import normalize
from src.common.utils import storage


@dataclass
class IdeaGraph:
    ideas: dict[str, IdeaRecord] = field(default_factory=dict)
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
                graph.ideas[idea_id] = normalize(raw)
            except Exception:  # noqa: BLE001 — a stale file shouldn't break the graph
                graph.skipped.append(idea_id)
        return graph

    def get(self, idea_id: str) -> IdeaRecord | None:
        return self.ideas.get(idea_id)

    def children_of(self, node_id: str) -> list[IdeaRecord]:
        """Ideas with an edge pointing at `node_id` — works for a gap id or an idea id.

        Two edge kinds, kept apart on the record: `addresses` holds the gaps an idea
        attacks, `relations` holds its typed edges to other ideas.
        """
        return [i for i in self.ideas.values()
                if node_id in i.addresses
                or any(r.target == node_id for r in i.relations)]

    def for_gap(self, gap_id: str) -> list[IdeaRecord]:
        """Ideas born directly from this gap."""
        return [i for i in self.ideas.values() if gap_id in i.addresses]

    def ancestors(self, idea_id: str) -> list[IdeaRecord]:
        """The idea's parent chain, nearest first. Stops at gaps (which aren't ideas).

        Follows `child-of` and `variant-of` only: those say where the record came from.
        `inspired-by` is a reference, not a lineage, and `merged-into` points the other
        way — following either would make the chain something other than an ancestry.
        """
        chain: list[IdeaRecord] = []
        seen = {idea_id}
        current = self.get(idea_id)
        while current:
            parent = next((r for r in current.relations
                           if r.type in ("child-of", "variant-of")), None)
            if parent is None or parent.target in seen:
                break
            seen.add(parent.target)
            current = self.get(parent.target)
            if current is None:
                break
            chain.append(current)
        return chain

    def roots(self) -> list[IdeaRecord]:
        """Ideas that came straight from a gap rather than from another idea."""
        return [i for i in self.ideas.values()
                if not any(r.type in ("child-of", "variant-of") for r in i.relations)]
