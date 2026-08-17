"""The idea schema — one type, whether the idea is a draft or committed.

Ontology (deliberately minimal for V1):

    gap.json  --addresses-->  idea.json  --child-->  idea.json

Nodes are gaps and ideas; a gap is where an idea originates, and an idea can be born from
another idea. There is no `variant`, no `merge`, no version chain yet — those get added
once real sessions show they're needed (see orchestrator_agent/SPEC.md).

Edges live on the *child* (`parents`), so creating one is a single-file write and two
concurrent sessions never contend. There is no central graph file; the graph is assembled
on read — see `common/tools/idea_graph.py`.

An idea gets its `idea_id` from the agent that creates it, not from the LLM inside that
agent, and starts as `status="draft"`. Drafts live only in the session's run result. They
become nodes in the graph when the user accepts them, which is the only thing that writes
to `ideas/`.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

ParentKind = Literal["gap", "idea"]
IdeaStatus = Literal["draft", "committed"]


class IdeaEdge(BaseModel):
    """One incoming edge: which node this idea was born from.

    `parent_kind` distinguishes the two allowed edges — gap -> idea (the idea addresses
    that gap) and idea -> idea (this idea is a child of that one).
    """

    parent_id: str
    parent_kind: ParentKind


class IdeaOrigin(BaseModel):
    """Provenance — which session and run produced this idea, and under which lens."""

    session_id: str = ""
    run_id: str = ""
    lens: Optional[str] = None


class Idea(BaseModel):
    """A research idea: workspace/<category>/ideas/<idea_id>.json once committed."""

    idea_id: str
    title: str
    short: str = Field(default="", description="One line, <80 chars — the graph label.")
    description: str = Field(default="", description="The idea and its mechanism.")
    method: list[str] = Field(
        default_factory=list,
        description="Concrete steps. An idea without steps is a direction, not an idea.",
    )
    parents: list[IdeaEdge] = Field(default_factory=list)
    related_papers: list[str] = Field(default_factory=list)
    thought_graph: list[dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "The reasoning that produced this idea, appended by each creative-thinker run. "
            "Opaque to the orchestrator — the thinker owns this shape."
        ),
    )
    status: IdeaStatus = "draft"
    origin: IdeaOrigin = Field(default_factory=IdeaOrigin)
    created_at: str = ""
    updated_at: str = ""
