"""`idea.json` — the record an idea is developed in, and the graph of its own thinking.

**Field names and enums here follow the design of record**, the vault note
`Agent System/Idea Agent/Creative Thinker (Tree of Thoughts)/idea.json` — including
`edges[].from` / `.to`, `relations[].target` / `.run`, `header.agent_styles`, the
`root | derived | merge | provisional` idea states, `provenance` as a *list* of tagged
claims, and `run_id` / `session_id` / `timestamp` on the run entry rather than in its
header. Anything this file adds beyond that note is additive and marked as such.

Four parts, with deliberately different rigidity:

- **spine** — `idea_id`, `title`, `state`, `one_liner`, `description`, `problem`,
  `addresses`, `relations`. Fixed, validated, and the only part the idea-graph panel
  reads. A
  *draft* (an idea with no workspace identity yet) is the same model with
  `idea_id = None` and `state = "provisional"` — minting an id is the orchestrator's job.
- **`sections`** — open. Lowercase slug keys, markdown or list values. **Deliberately
  not validated**: validating it would remove the flexibility the format exists for.
- **`evaluation` / `provenance`** — many writers, one record. Each agent owns its block
  and *appends*; nobody rewrites another's. Any factual claim entering the description is
  tagged with its source and the agent that brought it in.
- **`runs`** — the thinking graph. Mandatory and **append-only**: one entry per
  creative-thinker invocation. Consecutive runs concatenate into one continuous graph —
  generation run, then refinements weeks apart, then a split — so the file *is* the
  history of how the idea came to be. Never edited: the graph is evidence, and a
  rewritten trace is worthless. Each entry also carries a `RunSummary` — the
  compressed form, and the only part a *later* run reads, so the prompt does not grow
  with the file.

Nothing here writes to disk. Persistence is the orchestrator's (see
`src/agents/idea_agent/apply.py`), and it is the only writer.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Emit the vault's field names (``from``/``to``/``target``/``run``) on every dump,
# so what is written to disk and what is streamed match the spec rather than the
# Python attribute names that had to avoid keywords.
_ALIASED = ConfigDict(populate_by_name=True, serialize_by_alias=True)

# --- the thinking graph (runs[]) --------------------------------------------

NodeStatus = Literal["kept", "pruned", "rejected", "accepted"]
NodeKind = Literal["root", "candidate", "branch", "aggregate"]
EdgeType = Literal["expanded-into", "aggregated-from"]


class ThoughtNode(BaseModel):
    """One thought: a generation candidate, a refinement branch, or a merge.

    Spec fields: `node_id`, `parent_id`, `text`, `style`, `score`, `status`.
    Additive: `depth`, `kind`, `title`, `comparison`, `question`, `sources` — what the
    decision-graph window needs to lay the tree out, badge the style and show *why* a
    node survived without re-deriving it.

    `score` is the win rate a node earned against its siblings, not an absolute quality
    judgement: within one expansion the only question asked is which sibling is more
    promising.
    """

    node_id: str
    parent_id: str | None = None
    text: str = ""
    style: str = "neutral"
    score: float | None = None
    status: NodeStatus = "kept"

    depth: int = 0
    kind: NodeKind = "branch"
    title: str = ""
    comparison: str = ""
    question: str | None = None
    sources: list[str] = Field(default_factory=list)


class ThoughtEdge(BaseModel):
    """`expanded-into` (a node was deepened) or `aggregated-from` (a merge).

    Serialized as `{"from": ..., "to": ...}` per the spec; `from` is a Python keyword,
    hence the attribute names.
    """

    model_config = _ALIASED

    from_node: str = Field(alias="from")
    to_node: str = Field(alias="to")
    type: EdgeType = "expanded-into"


class RunHeader(BaseModel):
    """What the run was called with — enough to reproduce it and to compare runs.

    ``width`` and ``depth`` replace the single ``n`` the first draft carried. One
    number meaning "breadth here, depth there" could not express a refinement that
    branches *and* deepens, which is what the tree of thoughts actually does:
    ``width`` is how many siblings one expansion proposes (both modes), ``depth``
    how many levels refinement takes. Generation is ``depth = 1`` by definition.

    ``n`` is still accepted on the way in so run entries written before the split
    still load; it is mapped onto whichever axis the mode used it for.
    """

    model_config = ConfigDict(populate_by_name=True)

    mode: Literal["generation", "refinement"]
    width: int = 3
    depth: int = 1
    model: str = ""
    agent_styles: list[str] = Field(default_factory=list)
    context_ids: list[str] = Field(default_factory=list)
    triggering_action: str = ""

    tool_use: bool = False  # additive
    task: str = ""  # additive: the free instruction a refinement run was given

    @model_validator(mode="before")
    @classmethod
    def _accept_legacy_n(cls, data: Any) -> Any:
        """A pre-split entry carried ``n`` only — read it onto the axis it meant."""
        if not isinstance(data, dict) or "n" not in data:
            return data
        data = dict(data)
        n = data.pop("n")
        if str(data.get("mode")) == "refinement":
            data.setdefault("depth", n)
        else:
            data.setdefault("width", n)
            data.setdefault("depth", 1)
        return data


class RunSummary(BaseModel):
    """The run in a paragraph — and the **only** part of it a later run reads.

    ``runs[]`` grows forever, so it cannot be what the next prompt is built from: a
    dozen runs of raw branch text buries the idea it is supposed to be about. The
    full nodes stay on the record as evidence for the decision-graph window; this
    is the compressed form that travels into the next run's context.

    Written by ``assemble`` from the trace, so it can never disagree with it.
    """

    objective: str = ""  # what this run was asked to do, in one line
    accepted: str = ""  # what it settled, and why
    rejected: str = ""  # what it tried and dropped — the re-treading guard
    open: list[str] = Field(default_factory=list)  # what it could not close

    @model_validator(mode="before")
    @classmethod
    def _accept_legacy_string(cls, data: Any) -> Any:
        """Older entries stored one free line here; keep it as the objective."""
        if isinstance(data, str):
            return {"objective": data}
        return data

    def is_empty(self) -> bool:
        return not (self.objective or self.accepted or self.rejected or self.open)


class RunOutcome(BaseModel):
    """How the run ended.

    Spec fields: `accepted_node`, `escalation`, `truncated`. Additive: `status`,
    `reason`, `limit` and `accepted_nodes` — the reflector needs to tell
    `stopped-uncertain` from `truncated`, and `limit` names the ceiling that tripped so a
    partial exploration can never be mistaken for a finished one.
    """

    accepted_node: str | None = None
    escalation: str | None = None
    truncated: bool = False

    # Additive: the escalation's one-line diagnosis and the branch it concerns. Without
    # them the stored trace records *that* a split was proposed but not why, and the
    # suggestion itself lives only in the envelope, which is not persisted.
    escalation_node: str | None = None
    escalation_diagnosis: str = ""

    status: Literal["completed", "stopped-uncertain", "truncated"] = "completed"
    reason: str = ""
    limit: str | None = None
    accepted_nodes: list[str] = Field(default_factory=list)
    summary: RunSummary = Field(default_factory=RunSummary)
    answer: str = ""  # additive: a refinement run's reply to the task it was given
    # Additive, and the sibling of ``answer``: changes the run recommended to the
    # idea but did not make, because it was not asked to. Persisted rather than
    # left in the envelope so a later run can see the change was already put to the
    # user — otherwise every run re-derives the same recommendation and proposes it
    # again. One line each, rendered into the next run's prompt by
    # ``creative_thinker.tools.context.run_summaries``.
    proposed_changes: list[str] = Field(default_factory=list)


class RunEntry(BaseModel):
    """One creative-thinker invocation.

    Appended even when nothing was accepted: a run that produced nothing usable is still
    the record of what was tried, and the next run reads it so it does not re-tread the
    same ground.
    """

    run_id: str
    session_id: str | None = None
    timestamp: str = ""
    header: RunHeader
    nodes: list[ThoughtNode] = Field(default_factory=list)
    edges: list[ThoughtEdge] = Field(default_factory=list)
    outcome: RunOutcome = Field(default_factory=RunOutcome)


# --- the record --------------------------------------------------------------

# root: first formulation · derived: child or variant · merge: a synthesis ·
# provisional: sketched, not yet grounded — a draft the user has not accepted.
IdeaState = Literal["root", "derived", "merge", "provisional", "superseded"]


class IdeaRelation(BaseModel):
    """A typed edge to another idea. Written by the orchestrator only — the creative
    thinker *suggests* relations, it never mints one. Every edge carries its one-line
    rationale and the run that created it: edges are evidence of reasoning."""

    type: Literal["child-of", "variant-of", "merged-into", "inspired-by"]
    target: str
    rationale: str = ""
    run: str | None = None


class ProvenanceClaim(BaseModel):
    """One factual claim in the record, tagged with where it came from and who brought
    it in. A list, not a map: several agents append to the same record."""

    claim: str = ""
    source: str = ""
    via: str = ""  # the agent that brought it in


class IdeaOrigin(BaseModel):
    """Where this idea came from: the run, and the node in that run's trace.

    Additive, and it is what joins the two graphs. They stay connected by provenance
    rather than by sharing nodes — the idea graph holds accepted nodes only, and "how did
    this idea come about?" renders the trace on demand from this pointer.
    """

    run_id: str | None = None
    node_id: str | None = None
    node_path: list[str] = Field(default_factory=list)
    style: str = ""
    mode: str = ""
    parent_idea_id: str | None = None
    suggested: str | None = None  # the operation that proposed it, for a split

    # A child/variant is handed the parent's whole record — content *and* thinking
    # graph — so it reads standalone from day one. These two say where that copied
    # material came from, which is what keeps "copied" from being mistaken for
    # "independently arrived at" when the two files later diverge.
    inherited_from: str | None = None  # the parent whose record was copied
    inherited_upto_run: str | None = None  # the last parent run present in the copy


class InheritedFrom(BaseModel):
    """Where a child/variant copied its content from, and how far.

    Spine-level per the design of record (`idea.json.md`): "Inheritance is by
    reference. A child or variant copies the parent's description and sections …
    and carries the parent's run summaries plus ``inherited_from { idea_id,
    upto_run_id }``."

    It sits on the spine rather than inside ``origin`` because it is a claim about
    the *record* — which parts of it were not independently arrived at — while
    ``origin`` is a claim about the run that produced the idea. The two answer
    different questions and a reader needs the first without opening the second.
    """

    idea_id: str | None = None
    upto_run_id: str | None = None


class Evaluation(BaseModel):
    """The three axes, judged separately — a blended score hides which one failed.
    Extra keys are allowed so another agent can append its own block."""

    model_config = ConfigDict(extra="allow")

    novelty: float | None = None
    feasibility: float | None = None
    significance: float | None = None


class IdeaRecord(BaseModel):
    """One `idea.json`. `idea_id is None` marks a draft with no workspace identity."""

    # ``validate_assignment`` so a caller that assigns raw dicts (``idea.runs = [...]``)
    # still ends up with a schema-valid record: the trace is evidence, and evidence that
    # only validates on the way in is not worth much.
    model_config = ConfigDict(extra="allow", serialize_by_alias=True,
                              validate_assignment=True)

    # spine
    idea_id: str | None = None
    title: str = ""
    state: IdeaState = "provisional"
    one_liner: str = ""
    description: str = ""
    # What the idea is *for*: the problem it attacks or the objective it chases.
    # Spine rather than a section because every idea has one and the orchestrator
    # ranks, groups and dedups on it — a section could be renamed or deleted.
    problem: str = ""
    addresses: list[str] = Field(default_factory=list)
    relations: list[IdeaRelation] = Field(default_factory=list)

    # Set when this record's content was copied from a parent (a child or a
    # variant). ``origin.inherited_from`` / ``inherited_upto_run`` carry the same
    # two values on records written before this field existed; ``_lift_inherited``
    # reads those onto it so one accessor serves both.
    inherited_from: InheritedFrom | None = None

    # open part
    sections: dict[str, Any] = Field(default_factory=dict)
    # The reading order of ``sections``. Python dicts preserve insertion order, but
    # the record travels through JSON and through agents that rebuild the map, and
    # a reordering that only exists as key order is not a change anyone can see or
    # diff. Keys missing here fall in after the listed ones, in map order, so a
    # section added without touching this list is never hidden.
    section_order: list[str] = Field(default_factory=list)

    # many writers, one record
    evaluation: Evaluation = Field(default_factory=Evaluation)
    provenance: list[ProvenanceClaim] = Field(default_factory=list)
    origin: IdeaOrigin | None = None

    # the thinking graph — append-only
    runs: list[RunEntry] = Field(default_factory=list)

    created_at: str = ""
    updated_at: str = ""

    @model_validator(mode="before")
    @classmethod
    def _lift_inherited(cls, data: Any) -> Any:
        """Read a pre-spine ``origin.inherited_*`` pair onto ``inherited_from``."""
        if not isinstance(data, dict) or data.get("inherited_from"):
            return data
        origin = data.get("origin")
        if not isinstance(origin, dict) or not origin.get("inherited_from"):
            return data
        data = dict(data)
        data["inherited_from"] = {
            "idea_id": origin.get("inherited_from"),
            "upto_run_id": origin.get("inherited_upto_run"),
        }
        return data

    def ordered_sections(self) -> list[tuple[str, Any]]:
        """``sections`` in reading order — ``section_order`` first, then the rest.

        The only place that order is decided, so the file, the UI and any agent
        rendering the record all read it the same way.
        """
        listed = [(k, self.sections[k]) for k in self.section_order if k in self.sections]
        seen = {k for k, _ in listed}
        return listed + [(k, v) for k, v in self.sections.items() if k not in seen]
