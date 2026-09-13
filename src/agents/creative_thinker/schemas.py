"""Creative thinker — call spec, result envelope, and LLM leaf outputs.

Three groups:

1. **Inputs** — :class:`ThinkerCall`, the slots the orchestrator fills (mode, idea,
   context, task, styles, width, depth, tool_use) plus a :class:`Budget`. Fully
   serializable.
2. **Output** — :class:`Envelope`, the single return value. Identical shape in both
   modes and always complete: even a run that accepted nothing returns a valid
   ``idea`` with its trace appended and an empty ``drafts``.
3. **Leaf outputs** — the structured shapes the four LLM subagents must return.
   Every field has a default, so an offline/mock model still yields a
   structurally valid object.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from src.common.schemas.idea import IdeaRecord

Mode = Literal["generation", "refinement"]
Status = Literal["completed", "stopped-uncertain", "truncated"]
# Only ``create-variant`` is ever *raised* by the thinker today — the divergence
# offer described on :class:`SuggestedOperation`. The rest of the literal stays so
# stored envelopes from earlier runs still validate, and so the orchestrator has
# the vocabulary when it starts proposing operations of its own.
OperationType = Literal[
    "update-idea", "create-child", "create-variant", "merge", "investigate"
]


# --- inputs ------------------------------------------------------------------


class Budget(BaseModel):
    """Ceilings the plan schema sets. The thinker never raises them mid-run.

    Hitting one is the **normal** path, not the error path: there is no goal test
    in divergent thinking, so a run that stops at a ceiling returns the survivors
    of the last *completed* round, labelled ``truncated``.
    """

    max_llm_calls: int = 60
    max_investigations: int = 4
    max_seconds: float = 0.0  # 0 = no wall-clock ceiling


class AgentStyle(BaseModel):
    """A way of thinking, not a temperature knob.

    ``name`` selects the prompt folder under ``styles/``; ``model`` and
    ``temperature`` let a style bring its own decoding settings. The style is
    recorded on every node it produced, which is what makes the roster
    measurable over time.
    """

    name: str
    model: str = "reasoner"
    temperature: float = 0.7


class ThinkerCall(BaseModel):
    """The slots the orchestrator fills, plus budget and bookkeeping.

    ``idea`` is a **snapshot**. The thinker mutates its own deep copy of it and
    returns that; it never writes the original back and never touches the
    workspace.

    ``task`` is what makes refinement general. Without it a refinement run can only
    chase the open questions the idea already stored about itself — one plan schema
    ("deep dive") wearing the costume of a mode. With it the orchestrator can hand
    down anything a session actually asks for: *answer this · add a datasets section ·
    make it cheaper · argue against the premise*, and the whole tree grows toward
    that instead of toward the idea's own navel.

    ``width`` and ``depth`` replace the single overloaded ``n``. ``width`` is the
    branching factor of one expansion in either mode; ``depth`` is how many levels
    refinement takes (generation is depth 1 by definition). ``depth = 1`` is still
    the plain chain of thought, through this same code path.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, populate_by_name=True)

    mode: Mode = "generation"
    idea: IdeaRecord = Field(default_factory=IdeaRecord)
    context: dict[str, Any] = Field(default_factory=dict)
    task: str = ""
    styles: list[AgentStyle] = Field(default_factory=list)
    width: int = 3
    depth: int = 3
    tool_use: bool = False

    budget: Budget = Field(default_factory=Budget)
    triggering_action: str = ""
    run_id: str | None = None  # supplied for reproducible traces; else generated
    session_id: str | None = None  # recorded on the run entry; the thinker holds no session
    parallel: bool = True  # generation fan-out runs its styles concurrently

    @model_validator(mode="before")
    @classmethod
    def _accept_legacy_n(cls, data: Any) -> Any:
        """A caller still passing ``n`` gets it read onto the axis it used to mean.

        An explicit ``width``/``depth`` always wins over it.
        """
        if not isinstance(data, dict) or "n" not in data:
            return data
        data = dict(data)
        n = data.pop("n")
        if str(data.get("mode", "generation")) == "refinement":
            data.setdefault("depth", n)
        else:
            data.setdefault("width", n)
            data.setdefault("depth", 1)
        return data

    @model_validator(mode="after")
    def _single_pass_is_one_branch(self) -> "ThinkerCall":
        """Make refinement depth 1 the promised single chain, not a hidden fan-out.

        Refinement exposes only ``depth`` to the user. Leaving ``width`` at its default
        of three meant selecting depth 1 still created three sibling candidates before
        the evaluator chose among them. Besides being surprising, that contradicted the
        documented ``depth = 1`` chain-of-thought degeneration. A single pass therefore
        has exactly one branch regardless of a stale/default width supplied upstream.
        """
        if self.mode == "refinement" and self.depth == 1:
            self.width = 1
        return self


# --- output ------------------------------------------------------------------


class SuggestedOperation(BaseModel):
    """A typed proposal for the orchestrator — never an act.

    ``rationale`` is written to become the justification carried by the edge the
    orchestrator would create, so an accepted suggestion needs no re-derivation.

    **One occasion raises one.** A run that carried out its task and, in doing so,
    moved the idea far enough that it is no longer plainly the same idea offers the
    result as a ``create-variant`` instead of assuming the overwrite. Nothing else
    is a suggestion: a losing branch is a losing branch, and an unanswered question
    with ``tool_use`` off is what the user asked for by leaving it off.
    """

    type: OperationType
    node_id: str = ""  # the branch it came from
    diagnosis: str = ""  # one line
    rationale: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)

    # Proposals that only make sense together — the "make a variant of each branch"
    # offer raised when the selector could not choose. The escalation rate limit
    # thins *incidental* splits but never breaks a group apart, because half of an
    # either-or offer is not a smaller version of it, it is a wrong one.
    group: str = ""


class OpenQuestion(BaseModel):
    """A question for a human, pinned to the node that raised it."""

    node_id: str = ""
    question: str = ""
    options: list[str] = Field(default_factory=list)


class ProposedChange(BaseModel):
    """A change the run **recommends but deliberately did not make**.

    The thinker only ever does what it was asked. A run set a *question* answers
    it and records the finding; if that finding also implies the description or
    another section should change, saying so is the honest move and doing it is
    not — the run was not asked for it, and whether to adopt it is a decision that
    needs the conversation, which the thinker cannot see.

    So it lands here instead: named target, the change, and why. The orchestrator
    holds the chat history and either resolves it itself or puts it to the user;
    a "yes" comes back as the next run's ``task``, and *that* run makes the edit
    because that run was asked to.

    Not a ``SuggestedOperation``: those are proposals about the idea *graph* (a
    new node, a new edge). This is content, on one record, and needs no new
    escalation type.
    """

    # ``description`` | ``problem`` | a section slug (existing or new)
    target: str = ""
    change: str = ""  # what to change it to, concretely enough to act on
    reason: str = ""  # what this run found that makes the change worth making


class Metrics(BaseModel):
    """Spend and diversity. Diversity is computed deterministically from the
    node texts (see ``tools/diversity.py``) so it is comparable across runs."""

    llm_calls: int = 0
    investigations: int = 0
    tokens: int = 0
    duration_s: float = 0.0
    nodes: int = 0
    mean_pairwise_distance: float = 0.0
    cluster_count: int = 0


class Envelope(BaseModel):
    """Everything one call returns. The orchestrator decides what becomes global."""

    model_config = ConfigDict(validate_assignment=True)

    idea: IdeaRecord
    # The run's reply to ``task`` — substance, not phrasing. A refinement asked
    # "does this also work for video?" has to be able to *answer*; before this field
    # existed the answer had nowhere to go and the orchestrator had to re-derive it
    # from the description diff. The orchestrator still owns the voice: it decides
    # how this reaches the user, as it is the only component that speaks.
    answer: str = ""
    drafts: list[IdeaRecord] = Field(default_factory=list)
    suggested_operations: list[SuggestedOperation] = Field(default_factory=list)
    # Changes the run recommends to *this* idea but did not make, because it was
    # not asked to. The orchestrator turns one into the next run's task, or drops
    # it. Distinct from ``suggested_operations``, which are graph changes.
    proposed_changes: list[ProposedChange] = Field(default_factory=list)
    questions: list[OpenQuestion] = Field(default_factory=list)
    status: Status = "completed"
    status_reason: str = ""
    limit: str | None = None
    metrics: Metrics = Field(default_factory=Metrics)
    run_id: str = ""


# --- LLM leaf outputs --------------------------------------------------------

def _as_list(value: Any) -> Any:
    """Accept a string where a list of strings was asked for.

    Models hand back ``open_questions`` as one paragraph often enough that rejecting it
    is the wrong call: a `ValidationError` here kills a whole run over formatting, and
    the retry costs another full call. Bullet/newline-separated text is split; a single
    sentence becomes a one-item list.
    """
    if value is None:
        return []
    if not isinstance(value, str):
        return value
    lines = [line.strip(" -*\t") for line in value.splitlines()]
    items = [line for line in lines if line]
    return items if len(items) > 1 else ([value.strip()] if value.strip() else [])


# The spine of a generation candidate. Everything a model puts outside it was meant
# as content — see ``Candidate._fold_loose_keys_into_sections``.
_CANDIDATE_SPINE = ("title", "one_liner", "description", "problem", "sections")


class Section(BaseModel):
    """One section of an idea, named by the thinker rather than by this schema.

    ``key`` is a lowercase slug and ``value`` is markdown or a list. That is the
    whole contract — the design of record calls ``sections`` *"deliberately not
    validated"*, because what matters about an idea (techniques, baselines, a
    napkin formula, a discussion that settled something) differs per idea and is
    not knowable up front. Unknown keys render generically in the app, so a new
    one never breaks anything.
    """

    key: str = ""
    value: Any = ""


class Candidate(BaseModel):
    """One rough generation candidate — a proposal to react to, not a finished
    idea. No LLM judge filters these; the human is the evaluator."""

    title: str = ""
    one_liner: str = ""
    description: str = ""
    # What the candidate is *for*. Part of the spine of every stored idea, so a
    # generation run has to state it: a draft that lands without one leaves the
    # single field the orchestrator ranks and groups on empty, and nothing
    # downstream can reconstruct it from the description.
    problem: str = ""
    # The candidate's own sections, keyed by the thinker. This used to be three
    # literal fields — ``technique``, ``open_questions``, ``rationale`` — copied
    # into ``sections`` verbatim, which made every idea in the workspace carry the
    # same three keys and gave refinement nothing but those three slots to
    # overwrite. The starter vocabulary now lives in the prompt, where it is a
    # suggestion; only ``open_questions`` is read back by name (see
    # ``agent._open_questions``) and even that is not required.
    sections: list[Section] = Field(default_factory=list)

    @field_validator("sections", mode="before")
    @classmethod
    def _accept_a_sections_map(cls, value: Any) -> Any:
        """Accept ``{slug: value}`` where a list was asked for — same reading."""
        if isinstance(value, dict):
            return [{"key": k, "value": v} for k, v in value.items()]
        return value

    @model_validator(mode="before")
    @classmethod
    def _fold_loose_keys_into_sections(cls, data: Any) -> Any:
        """Take ``{"technique": ...}`` at the top level as a section, not as noise.

        ``technique``/``open_questions``/``rationale`` were literal fields here until
        sections became open, and a model handed a long prompt will sometimes still
        emit them flat — as it will emit ``baselines`` flat after being told to name
        its own. Pydantic's default is to drop unknown keys silently, which would
        turn that into an idea with no content at all rather than a visible error.
        Since the spine is a closed set, anything outside it is content by
        elimination, and folding it in is the only reading that loses nothing.
        """
        if not isinstance(data, dict):
            return data
        loose = {k: v for k, v in data.items()
                 if k not in _CANDIDATE_SPINE and not str(k).startswith("_")}
        if not loose:
            return data
        existing = data.get("sections")
        if isinstance(existing, dict):
            existing = [{"key": k, "value": v} for k, v in existing.items()]
        merged = list(existing or [])
        named = {str(s.get("key")) if isinstance(s, dict) else str(getattr(s, "key", ""))
                 for s in merged}
        # A key written in both places is not overwritten: ``sections`` was the
        # field actually asked for, so it wins.
        merged += [{"key": k, "value": v} for k, v in loose.items() if k not in named]
        return {**{k: v for k, v in data.items() if k not in loose}, "sections": merged}

    def section_map(self) -> dict[str, Any]:
        """The sections as a slug→value map, in the order the thinker wrote them.

        Empty keys are dropped and a repeated key keeps its first position with
        the later value — the record has to have one entry per name, and a model
        that names the same section twice meant to write it once.
        """
        out: dict[str, Any] = {}
        for section in self.sections:
            key = str(section.key or "").strip().lower().replace(" ", "_")
            if not key:
                continue
            if isinstance(section.value, list):
                out[key] = [str(v) for v in section.value]
            elif str(section.value).strip():
                out[key] = section.value
        return out


class Branch(BaseModel):
    """One refinement branch: a concrete way to develop the idea further."""

    focus: str = ""
    text: str = ""
    addresses_question: str = ""
    needs_fact: str = ""  # non-empty → this branch wants the Investigator


class BranchSet(BaseModel):
    branches: list[Branch] = Field(default_factory=list)


class BranchNote(BaseModel):
    """The selector's line about one branch: why it won, lost, or is still standing."""

    index: int = 0
    verdict: str = ""  # one sentence, stored on the node as the record of its fate
    diverges: Literal["child", "variant", "none"] = "none"


class BranchSelection(BaseModel):
    """**One** call, all siblings, one decision — the whole refinement evaluator.

    This replaced a round-robin of pairwise votes. The round-robin cost
    ``k(k-1)/2`` calls to answer a question the model can answer once while looking
    at every branch at the same time, and its arithmetic could manufacture a
    "winner" no single call had ever endorsed. Comparing all of them together is
    also the only way to see the shape the tie has: three branches that are three
    facets of one idea want a different answer from three that are three ideas.

    ``winner`` is the index of the branch to deepen. ``undecided`` is a **first-class
    answer**, not a failure: it stops the run, keeps every branch, and hands the
    choice up — the user then either picks one or asks for a variant per branch.
    Never break a tie to look decisive; an arbitrary winner discards a good branch
    and the damage is invisible afterwards.
    """

    winner: int | None = None
    undecided: bool = False
    reasoning: str = ""  # the deciding difference, or why it cannot be decided
    notes: list[BranchNote] = Field(default_factory=list)

    @model_validator(mode="after")
    def _agree_with_itself(self) -> "BranchSelection":
        """``winner`` and ``undecided`` are one decision stated twice; make them agree.

        A model that sets both, or neither, must not become an arbitrary pick — the
        safe reading of a self-contradictory answer is that it could not decide.
        """
        if self.undecided:
            object.__setattr__(self, "winner", None)
        elif self.winner is None:
            object.__setattr__(self, "undecided", True)
        return self

    def note_for(self, index: int) -> BranchNote | None:
        return next((n for n in self.notes if n.index == index), None)


SectionOp = Literal["set", "append", "delete", "rename", "reorder"]


class SectionEdit(BaseModel):
    """One change to the idea's open part, named rather than implied.

    The previous writeback returned a whole ``{slug: markdown}`` blob and merged it
    additively, which meant a section could never be **deleted** — the record only
    ever grew, and pruning it was impossible without a human editing the file. An
    omitted key still means "leave it alone" here; deleting now requires saying so,
    which is both auditable and impossible to do by accident.
    """

    op: SectionOp = "set"
    key: str = ""  # the section slug (unused by ``reorder``)
    value: Any = ""  # ``set`` / ``append``
    to: str = ""  # ``rename``
    order: list[str] = Field(default_factory=list)  # ``reorder``


class IdeaEdit(BaseModel):
    """The rewrite applied to the *one* idea under refinement, plus the run's answer.

    Refinement refines; it does not invent — new ideas appear only through a
    suggested split.
    """

    # The two title-field lines. Empty leaves them alone, which is the usual case —
    # they change only when the task asked for it. They exist as fields because the
    # prompt has always told the model they do: without them a task like "rewrite the
    # one-liner" is unsatisfiable, and the model does the only thing left to it and
    # puts the new sentence in the prose answer, where nothing applies it.
    title: str = ""
    one_liner: str = ""
    description: str = ""
    problem: str = ""
    edits: list[SectionEdit] = Field(default_factory=list)
    # Recommended, not applied — see :class:`ProposedChange`.
    proposed_changes: list[ProposedChange] = Field(default_factory=list)
    # The reply to the task. A run whose task was a question ("would this work for
    # video?") may legitimately change nothing and still owe an answer.
    answer: str = ""
    change_summary: str = ""
    open_questions: list[str] = Field(default_factory=list)
    # The one thing a refinement run is allowed to escalate: it did what it was
    # asked, and doing it pushed the record far enough from the idea it started
    # from that overwriting is a judgement call rather than an update. The thinker
    # never decides that — it says so, and the user picks variant or overwrite.
    diverged: bool = False
    divergence_reason: str = ""
    variant_title: str = ""

    _coerce_questions = field_validator("open_questions", mode="before")(_as_list)

    @field_validator("edits", mode="before")
    @classmethod
    def _accept_a_sections_map(cls, value: Any) -> Any:
        """Accept ``{slug: markdown}`` where a list of ops was asked for.

        Models fall back to the flat map often enough that refusing it would kill a
        whole run over formatting — and the reading is unambiguous: every entry is a
        ``set``. Only the *destructive* ops have to be spelled out.
        """
        if isinstance(value, dict):
            return [{"op": "set", "key": k, "value": v} for k, v in value.items()]
        return value
