"""The thinking graph, assembled as the run happens.

One :class:`TraceBuilder` per invocation. It does three things at once, and that
is the point — they must not drift apart:

1. mints the thought **nodes and edges** that become the ``runs[]`` entry,
2. **emits** each of them into the LangGraph custom stream, so the run log shows
   the tree growing instead of a spinner (branch created / scored / pruned /
   investigated / escalated), and
3. hands back the finished :class:`RunEntry` for :func:`append_run`, which is the
   only function anywhere that touches ``idea.runs`` — and it only ever appends.

Trace assembly is deterministic and model-free: node ids are ``<run_id>:n<k>``
in creation order, so a replayed run yields identical ids.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from src.common.schemas.idea import (
    IdeaRecord,
    NodeStatus,
    RunEntry,
    RunHeader,
    RunOutcome,
    ThoughtEdge,
    ThoughtNode,
)
from src.common.utils.logging.trace import emit


def new_run_id() -> str:
    return f"run_{uuid4().hex[:8]}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class TraceBuilder:
    """Accumulates one run's nodes, edges and outcome.

    ``run_id`` / ``session_id`` / ``timestamp`` live on the run *entry*, not in its
    header — as in the format spec — so they are held here rather than on
    :class:`RunHeader`.
    """

    def __init__(self, run_id: str, header: RunHeader, session_id: str | None = None):
        self.run_id = run_id
        self.session_id = session_id
        self.timestamp = _now_iso()
        self.header = header
        self.nodes: dict[str, ThoughtNode] = {}
        self.edges: list[ThoughtEdge] = []
        self._counter = 0

    # --- construction ---
    def add_node(self, *, text: str, style: str = "neutral", kind: str = "branch",
                 parent_id: str | None = None, depth: int = 0, title: str = "",
                 status: NodeStatus = "kept", question: str | None = None,
                 sources: list[str] | None = None) -> ThoughtNode:
        """Create a node, link it to its parent, and stream a ``branch created``."""
        node_id = f"{self.run_id}:n{self._counter}"
        self._counter += 1
        node = ThoughtNode(
            node_id=node_id, parent_id=parent_id, depth=depth, kind=kind,
            style=style, title=title, text=text, status=status, question=question,
            sources=sources or [],
        )
        self.nodes[node_id] = node
        if parent_id:
            self.add_edge(parent_id, node_id, "expanded-into")
        emit({"type": "event", "name": "branch created", "data": {
            "node_id": node_id, "parent_id": parent_id, "depth": depth,
            "style": style, "kind": kind, "title": title, "text": text[:400],
        }})
        return node

    def add_edge(self, source: str, target: str, type_: str = "expanded-into") -> None:
        self.edges.append(ThoughtEdge(from_node=source, to_node=target, type=type_))

    def aggregate(self, sources: list[str], target: str) -> None:
        """Record a merge: the target was ``aggregated-from`` several sources."""
        for s in sources:
            self.add_edge(s, target, "aggregated-from")

    # --- scoring / status ---
    def score(self, node_id: str, score: float, comparison: str = "") -> None:
        node = self.nodes[node_id]
        node.score = score
        node.comparison = comparison or node.comparison
        emit({"type": "event", "name": "branch scored", "data": {
            "node_id": node_id, "score": score, "comparison": comparison,
        }})

    def set_status(self, node_id: str, status: NodeStatus, comparison: str = "") -> None:
        """Pruned siblings are **recorded, never dropped** — the losing branch is
        exactly what stops the next run re-treading this ground."""
        node = self.nodes[node_id]
        node.status = status
        if comparison:
            node.comparison = comparison
        emit({"type": "event", "name": f"branch {status}", "data": {
            "node_id": node_id, "comparison": node.comparison,
        }})

    def attach_question(self, node_id: str, question: str) -> None:
        self.nodes[node_id].question = question

    # --- notes that are not nodes ---
    def note_investigation(self, node_id: str, question: str, found: bool) -> None:
        """Record a call that **actually happened**.

        Only reached when the Investigator was really invoked — a run with
        ``tool_use`` off must not emit `investigator called` events for calls it
        declined to make. See ``tools/investigate.Finding.attempted``.
        """
        emit({"type": "event", "name": "investigator called", "data": {
            "node_id": node_id, "question": question, "answered": found,
        }})

    def note_escalation(self, kind: str, node_id: str, diagnosis: str) -> None:
        emit({"type": "event", "name": "escalation raised", "data": {
            "kind": kind, "node_id": node_id, "diagnosis": diagnosis,
        }})

    # --- finish ---
    def texts(self) -> list[str]:
        return [n.text for n in self.nodes.values()]

    def build(self, outcome: RunOutcome) -> RunEntry:
        return RunEntry(
            run_id=self.run_id,
            session_id=self.session_id,
            timestamp=self.timestamp,
            header=self.header,
            nodes=list(self.nodes.values()),
            edges=list(self.edges),
            outcome=outcome,
        )


def make_header(*, mode: str, width: int, depth: int, model: str,
                agent_styles: list[str], context_ids: list[str],
                triggering_action: str, tool_use: bool, task: str = "") -> RunHeader:
    return RunHeader(
        mode=mode, width=width, depth=depth, model=model, agent_styles=agent_styles,
        context_ids=context_ids, triggering_action=triggering_action,
        tool_use=tool_use, task=task,
    )


def append_run(idea: IdeaRecord, entry: RunEntry) -> IdeaRecord:
    """Append one run to the idea's thinking graph. The **only** writer of
    ``runs[]``, and append-only by construction: existing entries are neither
    edited nor reordered, so the accumulated graph stays evidence."""
    idea.runs = list(idea.runs) + [entry]
    return idea
