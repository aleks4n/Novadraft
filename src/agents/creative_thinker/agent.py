"""Creative thinker — the divergent worker of the idea agent.

A **pure, callable worker with no side effects**. The orchestrator calls it, it
thinks, it returns. Between those two moments it touches nothing outside its own
inputs: no workspace access, no session state, no chat channel. It cannot create
an idea, write a file, or speak to the user — everything it produces goes back in
one :class:`Envelope` and the orchestrator decides what becomes global.

One graph, two parameter sets::

    prepare ─┬─ generate                    (fan-out: `width` styles in parallel, depth 1)
             └─ refine  [subgraph]          (tree of thoughts: expand → evaluate ⟲, ≤ `depth`)
                          └→ writeback
             └→ assemble → END

There is no third mode: a chain of thought is ``refinement`` with ``depth = 1``
through this same code path, and a graph of thoughts is refinement plus the
aggregate operator. Merging is refinement seeded from two source ideas.

Refinement is driven by ``spec.task`` — the free instruction the orchestrator passes
down. That is what lets one mode serve a question, a change, an addition, a critique
or an open develop, instead of only ever expanding the idea's own stored open
questions. A refinement run therefore owes two things back: the edited idea *and* an
``answer`` to the task it was set. Those two are its whole output — it returns no
drafts, and it raises **one** kind of suggestion and only when the write-up says the
result has stopped being the same idea (:func:`_divergence`). Nothing else escalates:
a losing branch is a verdict on the record, and a question the Investigator could not
settle is an open question, not a proposal.

**Stateless across calls.** Everything it knows about the idea's history comes
from the copy it was handed — specifically from that copy's ``runs[]``, which is
why the trace is append-only and mandatory.

**Anytime.** Stopped at any round it still returns a usable result, labelled
partial. Because divergent thinking has no goal test, hitting a ceiling is the
normal path, not the error path.

Entry points:

- :func:`think` — the callable interface for the orchestrator. Pure: it neither
  writes a file nor logs to disk.
- :func:`run` — the dev-harness/standalone entry point. Same graph, streamed
  through a :class:`GraphRunLog` so the run is recorded as a ``.jsonl``. It reads
  the idea (a fixture, or ``ws:<slug>/<idea_id>`` for a real workspace record) and
  writes a run log. It performs **no workspace write** unless explicitly asked with
  ``persist=<slug>``, and that write is the *orchestrator's* step, applied to the
  returned envelope after the agent is done — see
  :func:`src.agents.idea_agent.apply_envelope`.
"""

from __future__ import annotations

import json
import sys
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from pathlib import Path
from typing import Any, Callable

from langgraph.graph import END, START, StateGraph

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.agents.creative_thinker import styles as style_roster
from src.agents.creative_thinker.schemas import (
    AgentStyle,
    BranchSelection,
    Budget,
    Candidate,
    Envelope,
    IdeaEdit,
    OpenQuestion,
    ProposedChange,
    SuggestedOperation,
    ThinkerCall,
)
from src.agents.creative_thinker.state import CreativeThinkerState
from src.agents.creative_thinker.subagents.apply_edits.agent import apply_edits, merge_edit
from src.agents.creative_thinker.subagents.expand_branch.agent import expand_branch
from src.agents.creative_thinker.subagents.generate_candidate.agent import generate_candidate
from src.agents.creative_thinker.subagents.select_branch.agent import select_branch
from src.agents.creative_thinker.tools import context as ctx
from src.agents.creative_thinker.tools import investigate
from src.agents.creative_thinker.tools.budget import BudgetLedger
from src.agents.creative_thinker.tools.dedup import dedupe
from src.agents.creative_thinker.tools.envelope import build_envelope
from src.agents.creative_thinker.tools.rank import rank_candidates
from src.agents.creative_thinker.tools.trace import TraceBuilder, make_header, new_run_id
from src.common.schemas.idea import IdeaOrigin, IdeaRecord, InheritedFrom, RunSummary
from src.common.schemas.idea_compat import normalize as normalize_idea
from src.common.utils import storage
from src.common.utils.logging.runlog import GraphRunLog
from src.common.utils.logging.trace import log_event

# A grounded rating is a *rating*, never a filter: an Investigator pass shifts a
# candidate's rank, it never removes it from the batch.
_RATING_VERIFIED = 0.7
_RATING_UNVERIFIED = 0.5


# --- shared helpers ----------------------------------------------------------


def _candidate_text(c: Candidate) -> str:
    """The candidate as one blob — what dedup and the diversity metric compare on.

    Every section counts, not a fixed subset: with ``sections`` open, the part that
    distinguishes two candidates may live under any key, and a name this function
    does not know about is exactly the case it must not drop.
    """
    parts = [c.title, c.one_liner, c.description]
    for value in c.section_map().values():
        parts.append("\n".join(str(v) for v in value) if isinstance(value, list)
                     else str(value))
    return "\n".join(p for p in parts if p)


def _map_styles(styles: list[AgentStyle], fn: Callable[[AgentStyle], Any],
                parallel: bool) -> list[Any]:
    """Run ``fn`` once per style, concurrently when asked, results in style order.

    Each worker runs inside a *copy of the calling context*, so LangGraph's
    stream writer and node metadata survive the thread hop — the per-candidate
    LLM calls still land in the run log under the node that spawned them. Results
    keep the input order regardless of completion order, so the batch (and
    therefore the dedup and the ranking) is reproducible.
    """
    if not parallel or len(styles) <= 1:
        return [fn(s) for s in styles]
    with ThreadPoolExecutor(max_workers=len(styles)) as pool:
        futures = [pool.submit(copy_context().run, fn, s) for s in styles]
        return [f.result() for f in futures]


def _path_md(state: CreativeThinkerState) -> str:
    """The surviving branch, root first — the reasoning a node is expanded from."""
    trace: TraceBuilder = state["trace"]
    parts = []
    for node_id in state.get("path", []):
        node = trace.nodes[node_id]
        parts.append(f"### depth {node.depth} — {node.title or node.kind}\n{node.text}")
    return "\n\n".join(parts)


# --- prepare -----------------------------------------------------------------


def prepare(state: CreativeThinkerState) -> dict:
    """Take the deep copy, open the trace and the ledger, render the prompts.

    The copy is what makes the agent safe to call: from here on nothing reads or
    writes the caller's object. The root node anchors the run's thought graph to
    the idea it started from, so consecutive runs concatenate into one graph.
    """
    spec: ThinkerCall = state["spec"]
    idea = spec.idea.model_copy(deep=True)

    picked = (
        style_roster.pick_styles(spec.styles, max(1, spec.width))
        if spec.mode == "generation"
        else [style_roster.refinement_style(spec.styles)]
    )
    header = make_header(
        mode=spec.mode,
        width=max(1, spec.width),
        depth=1 if spec.mode == "generation" else max(1, spec.depth),
        model=(picked[0].model if picked else "reasoner"),
        agent_styles=[s.name for s in picked],
        context_ids=ctx.context_ids(spec.context),
        triggering_action=spec.triggering_action,
        tool_use=spec.tool_use,
        task=spec.task,
    )
    trace = TraceBuilder(spec.run_id or new_run_id(), header, session_id=spec.session_id)
    idea_md = ctx.render_idea(idea)
    # Every run's graph is rooted in what it started *from*, so consecutive runs
    # concatenate instead of producing a forest. Generating from gaps there is no
    # idea yet, and the root stands for the brief the run was given — the task, or
    # the loaded context — rather than a nameless "(brief)" placeholder.
    trace.add_node(
        text=idea_md, kind="root", style="-", depth=0, status="kept",
        title=(idea.title
               or (spec.task.strip().splitlines()[0][:80] if spec.task.strip() else "")
               or ("the brief: " + ", ".join(header.context_ids[:3])
                   if header.context_ids else "the brief")),
    )

    log_event("run prepared", mode=spec.mode, width=spec.width, depth=header.depth,
              styles=[s.name for s in picked], tool_use=spec.tool_use,
              task=spec.task, context_ids=header.context_ids)

    return {
        "idea": idea,
        "trace": trace,
        "ledger": BudgetLedger(spec.budget),
        "context_md": ctx.render_context(spec.context),
        "idea_md": idea_md,
        "prior_ground": ctx.prior_ground(idea),
        "existing_ideas": ctx.existing_idea_texts(spec.context),
        "depth": 0,
        "path": [next(iter(trace.nodes))],
        "frontier": [],
        "findings": [],
        "drafts": [],
        "operations": [],
        "questions": [],
        "status": "completed",
        "status_reason": "",
        "summary": "",
        "answer": "",
        "rejected_notes": [],
    }


def route(state: CreativeThinkerState) -> str:
    return "generate" if state["spec"].mode == "generation" else "refine"


# --- generation --------------------------------------------------------------


def generate(state: CreativeThinkerState) -> dict:
    """Fan out for breadth: ``width`` differently-styled thinkers, one candidate each.

    Depth stays 1 and the candidates are deliberately rough. **No LLM judge
    filters them** — they are deduplicated against the batch and against the
    ideas already in context, ranked, and every survivor is returned. The human
    is the evaluator.

    Same machinery serves all three of its jobs — new ideas from a gap, candidate
    children, candidate variants. What differs is only whether ``spec.idea`` carries
    a parent, and what that means for the drafts (see :func:`_draft_from`).
    """
    spec: ThinkerCall = state["spec"]
    trace: TraceBuilder = state["trace"]
    ledger: BudgetLedger = state["ledger"]
    root = state["path"][0]

    picked = style_roster.pick_styles(spec.styles, max(1, spec.width))
    if ledger.can_spend_llm(len(picked)):
        affordable = picked
    else:
        # Not every style fits under the ceiling: run as many *whole* candidates
        # as the budget allows and label the run truncated. A partial fan-out
        # must never be mistakable for the breadth that was asked for.
        room = max(0, spec.budget.max_llm_calls - ledger.llm_calls)
        affordable = picked[:room]

    def one(style: AgentStyle) -> Candidate:
        return generate_candidate(
            style=style,
            context_md=_briefed_context(state),
            idea_md=state["idea_md"] if state["idea"].title else "",
            prior_ground=state["prior_ground"],
            avoid=state["existing_ideas"],
        )

    candidates = _map_styles(affordable, one, spec.parallel)
    for _ in candidates:
        ledger.spend_llm()

    # --- nodes for every candidate, including the ones dedup will reject ---
    node_ids = [
        trace.add_node(text=_candidate_text(c), title=c.title, style=s.name,
                       kind="candidate", parent_id=root, depth=1).node_id
        for c, s in zip(candidates, affordable)
    ]

    texts = [_candidate_text(c) for c in candidates]
    dd = dedupe(texts, state["existing_ideas"])
    for index, reason in dd.dropped:
        trace.set_status(node_ids[index], "rejected", reason)

    # --- optional grounded rating (never a filter) ---
    # Skipped entirely without an Investigator. It used to run regardless, which cost
    # nothing but returned the same constant for every candidate — so the ranking below
    # silently degenerated to distinctness alone while the stream reported an
    # `investigator called` event per candidate that had never happened.
    ratings: dict[int, float] = {}
    if spec.tool_use and state.get("investigator") is not None:
        for index in dd.kept:
            finding = investigate.ask(
                f"Has this been proposed before, and does it hold up? "
                f"{candidates[index].title}: {candidates[index].one_liner}",
                investigator=state.get("investigator"), tool_use=spec.tool_use,
                ledger=ledger,
            )
            if finding.attempted:
                trace.note_investigation(node_ids[index], candidates[index].title,
                                         finding.found)
            ratings[index] = _RATING_VERIFIED if finding.found else _RATING_UNVERIFIED
            if finding.found:
                trace.nodes[node_ids[index]].sources = finding.sources or []

    # --- rank the survivors and carve them into drafts ---
    # Only the candidates that actually got a grounded rating carry one; without an
    # Investigator the map is empty and the ranking is distinctness alone, which is
    # the honest ordering rather than a constant dressed up as a score.
    ranked = rank_candidates([texts[i] for i in dd.kept], state["existing_ideas"],
                             {p: ratings[i] for p, i in enumerate(dd.kept)
                              if i in ratings})
    drafts: list[IdeaRecord] = []
    for position, entry in enumerate(ranked):
        index = dd.kept[entry.index]
        node_id = node_ids[index]
        trace.score(node_id, entry.score, f"rank {position + 1}/{len(ranked)}")
        trace.set_status(node_id, "kept")
        drafts.append(_draft_from(candidates[index], state, affordable[index], node_id))

    truncated = len(affordable) < len(picked)
    if truncated:
        ledger.note_limit("max_llm_calls")

    dropped = [f"{candidates[i].title or f'candidate {i}'} ({why})" for i, why in dd.dropped]
    return {
        "drafts": drafts,
        "status": "truncated" if truncated else "completed",
        "status_reason": (
            f"budget allowed {len(affordable)} of {len(picked)} styled thinkers"
            if truncated else f"{len(drafts)} candidates survived deduplication"
        ),
        "summary": RunSummary(
            objective=(
                spec.task
                or (f"generate candidate variants of {state['idea'].title}"
                    if state["idea"].title
                    else "generate candidate ideas from the loaded gaps")
            ),
            accepted="; ".join(d.title for d in drafts) or "nothing",
            rejected="; ".join(dropped),
            open=[q for d in drafts
                  for q in list((d.sections or {}).get("open_questions") or [])][:5],
        ),
        "rejected_notes": dropped,
    }


def _briefed_context(state: CreativeThinkerState) -> str:
    """The loaded context, with the run's task at the top when there is one.

    Generation is also given a task now — "propose variants that drop the transformer
    entirely" is a real instruction and it used to reach the thinkers only as an
    undifferentiated line inside ``user_input``, if at all.
    """
    task = state["spec"].task.strip()
    if not task:
        return state["context_md"]
    return f"## what this run was asked to do\n{task}\n\n{state['context_md']}"


def _inherit(parent: IdeaRecord) -> dict:
    """The parent's whole record as the starting point for a child or variant.

    A child is not a pointer to its parent, it is **an idea**: opened on its own it
    has to read as one, with the sections the parent accumulated and the thinking
    that produced them. Carrying only a ``parent_idea_id`` made a child that looked
    abandoned the moment you opened it — a title, a one-liner and nothing else.

    So content *and* ``runs[]`` are copied. The copied graph is the child's real
    history: it is a variant *of that thinking*, and a reader asking "why is it built
    this way?" needs the branches that decided it, not a cross-file reference.
    ``origin.inherited_from`` / ``inherited_upto_run`` mark where the copy stops, so
    the shared prefix stays identifiable after the two files diverge.

    Returns keyword arguments rather than a record: each caller has its own idea of
    what the child *changes*, and those must land on top of this.
    """
    return {
        "problem": parent.problem,
        # ``addresses`` is deliberately **not** copied. A child reaches its parent's
        # gaps through the derivation edge; restating them on the child says nothing
        # new and makes every descendant a duplicate of the parent's fan in the idea
        # graph. A child lists only the gaps it *additionally* attacks.
        "sections": deepcopy(dict(parent.sections or {})),
        # The parent's reading order comes with its sections; a child that reshuffled
        # them on copy would read as a different document for no reason.
        "section_order": list(parent.section_order or parent.sections or {}),
        # Spine-level, per the design of record: which record this content was copied
        # from and how far. ``origin`` says which *run* produced the idea — a
        # different question, and a reader needs this one without opening that.
        "inherited_from": InheritedFrom(
            idea_id=parent.idea_id,
            upto_run_id=parent.runs[-1].run_id if parent.runs else None,
        ) if parent.idea_id else None,
        "evaluation": parent.evaluation.model_copy(deep=True),
        "provenance": [c.model_copy(deep=True) for c in parent.provenance],
        # Copied, not shared: the child's graph continues on its own from here.
        "runs": [r.model_copy(deep=True) for r in parent.runs],
    }


def _origin(state: CreativeThinkerState, *, node_id: str, style: str, mode: str,
            path: list[str], suggested: str | None = None) -> IdeaOrigin:
    """Where a draft came from — and, when it inherited, what it inherited from."""
    parent = state["idea"]
    inherited = bool(parent.idea_id)
    return IdeaOrigin(
        run_id=state["trace"].run_id,
        node_id=node_id,
        node_path=path,
        style=style,
        mode=mode,
        parent_idea_id=parent.idea_id,
        suggested=suggested,
        inherited_from=parent.idea_id if inherited else None,
        inherited_upto_run=(parent.runs[-1].run_id if inherited and parent.runs else None),
    )


def _draft_from(candidate: Candidate, state: CreativeThinkerState,
                style: AgentStyle, node_id: str) -> IdeaRecord:
    """A candidate as a **draft** ``idea.json`` — deliberately without an id.

    Minting ``idea_id`` and writing relations is the orchestrator's job, so a draft
    carries neither; it points back at the node and run that produced it and nothing
    else.

    With a parent in ``spec.idea`` this run is producing children or variants, and the
    draft starts as a full copy of that parent (:func:`_inherit`) with the candidate
    written over the parts it actually changes. Without one it is a new idea from a
    gap and there is nothing to inherit.
    """
    parent = state["idea"]
    gap_ids = [i for i in ctx.context_ids(state["spec"].context) if i.startswith("gap")]
    base = _inherit(parent) if parent.idea_id else {}

    draft = IdeaRecord(
        **base,
        idea_id=None,
        title=candidate.title,
        # sketched, not yet grounded — it becomes root/derived when the user accepts it
        state="provisional",
        one_liner=candidate.one_liner,
        description=candidate.description,
        origin=_origin(
            state, node_id=node_id, style=style.name, mode="generation",
            path=[state["path"][0], node_id],
            # With a parent, a fan-out produces **variants**, and that is structural
            # rather than a guess: each styled thinker attacks the parent's gap with
            # its own mechanism, which is the definition of a variant. A *child* is
            # the same mechanism made more specific — that only ever comes out of
            # refinement, where the selector can see the parent's mechanism being
            # narrowed. Without a parent there is no edge to write at all.
            suggested=("create-variant" if parent.idea_id else None),
        ),
    )
    # The candidate's own contribution, written over whatever it inherited. With a
    # parent, that is the gaps this run had in view *minus* the ones the parent
    # already claims — the additions are what the child is actually asserting.
    inherited_gaps = set(parent.addresses or ())
    draft.addresses = [g for g in gap_ids if g not in inherited_gaps]
    # ``problem`` is spine, and every draft leaves here with one. The thinker is
    # asked for it directly; the parent's is the fallback for a variant that did not
    # move what the idea is *for*, and the one-liner is the last resort — an idea
    # whose stated objective is its own claim is thin, but an empty spine field is
    # a hole the orchestrator ranks and groups on.
    draft.problem = (
        candidate.problem.strip()
        or (draft.problem or "").strip()
        or candidate.one_liner.strip()
    )
    # The candidate names its own sections. Three literal keys used to be written
    # here from three literal schema fields, which is why every idea in the
    # workspace carried the same three and no run ever had a reason to add a
    # fourth. Inherited sections stay unless the candidate wrote over them by name.
    draft.sections = {**(draft.sections or {}), **candidate.section_map()}
    # Every record states its own reading order, generation included. Leaving it
    # empty here made a fresh idea the one record whose order lived only as dict
    # key order — the exact thing ``section_order`` exists to stop.
    draft.section_order = list(draft.sections)
    return draft


def expand(state: CreativeThinkerState) -> dict:
    """One level of the tree: ``width`` ways of doing the task from where we stand.

    The **task** is what is branched on. The idea's own recorded open questions are
    passed along as material — a branch may well take one up — but they are no longer
    the agenda, because a session asks for things the idea never thought to ask about
    itself.
    """
    spec: ThinkerCall = state["spec"]
    trace: TraceBuilder = state["trace"]
    ledger: BudgetLedger = state["ledger"]
    style = style_roster.refinement_style(spec.styles)
    tip = state["path"][-1]

    if not ledger.can_spend_llm():
        return {"frontier": [], "status": "truncated",
                "status_reason": f"budget exhausted before depth {state['depth'] + 1}"}

    branches = expand_branch(
        style=style,
        idea_md=state["idea_md"],
        path_md=_path_md(state),
        open_questions=_open_questions(state["idea"]),
        task=spec.task,
        width=spec.width,
        context_md=state["context_md"],
        prior_ground=state["prior_ground"],
        findings="\n".join(state.get("findings") or []),
    )
    ledger.spend_llm()

    if not branches:
        return {"frontier": [], "status_reason": "no further branches proposed"}

    depth = state["depth"] + 1
    frontier = [
        trace.add_node(text=b.text or b.focus, title=b.focus, style=style.name,
                       kind="branch", parent_id=tip, depth=depth).node_id
        for b in branches
    ]

    # A branch that needs a fact waits for it rather than inventing it. A fact the
    # run could not settle stays on the node and, when it survives, in the idea's
    # open questions — it is never escalated as a proposal. With ``tool_use`` off
    # the Investigator was not wanted, and proposing it anyway argues with the
    # setting; with it on and empty-handed, an unanswered question is an open
    # question, which the record already has a place for.
    findings = list(state.get("findings") or [])
    for branch, node_id in zip(branches, frontier):
        if not branch.needs_fact.strip():
            continue
        finding = investigate.ask(branch.needs_fact, investigator=state.get("investigator"),
                                  tool_use=spec.tool_use, ledger=ledger)
        if finding.attempted:
            trace.note_investigation(node_id, branch.needs_fact, finding.found)
        if finding.found:
            findings.append(f"- Q: {branch.needs_fact}\n  A: {finding.answer}")
            trace.nodes[node_id].sources = finding.sources or []
        else:
            trace.attach_question(node_id, branch.needs_fact)

    return {"frontier": frontier, "depth": depth, "findings": findings}


def evaluate(state: CreativeThinkerState) -> dict:
    """Choose the branch to deepen — **one call, the whole frontier**.

    The evaluator is the model itself and it is asked only a relative question: given
    the idea and the task, which of these is worth developing further? It never scores
    a branch.

    **undecided** is not "pick one": it ends the run ``stopped-uncertain`` with every
    branch left ``kept`` and one question carrying them as options, for the user to
    answer. Deciding it by arithmetic — as the previous round-robin did — could crown
    a branch no comparison had endorsed.

    A *losing* branch produces nothing but its verdict line. It used to come back as
    a ``create-child`` / ``create-variant`` proposal whenever the selector called it
    divergent, which turned every run into a shop window of roads not taken. Whether
    the run's **result** has diverged is asked once, at the write-up, about the thing
    that actually exists (:func:`writeback`).
    """
    spec: ThinkerCall = state["spec"]
    trace: TraceBuilder = state["trace"]
    ledger: BudgetLedger = state["ledger"]
    style = style_roster.refinement_style(spec.styles)
    frontier = state.get("frontier") or []

    if not frontier:
        return {}
    if len(frontier) == 1:  # only one honest continuation — nothing to choose between
        trace.score(frontier[0], 1.0, "sole branch at this depth")
        return {"path": state["path"] + [frontier[0]]}

    if not ledger.can_spend_llm():
        # Cannot afford the selection: drop this unevaluated round and return the
        # survivors of the last *completed* one. An unevaluated branch is not a result.
        for node_id in frontier:
            trace.set_status(node_id, "pruned", "round not evaluated — budget exhausted")
        return {"frontier": [], "status": "truncated",
                "status_reason": f"budget exhausted before evaluating depth {state['depth']}"}

    selection = select_branch(
        style=style,
        idea_md=state["idea_md"],
        texts=[trace.nodes[i].text for i in frontier],
        titles=[trace.nodes[i].title for i in frontier],
        task=spec.task,
        context_md=state["context_md"],
    )
    ledger.spend_llm()

    # Every branch keeps the selector's line about it, winner or not: that sentence is
    # the permanent record of why this branch died, and it is what stops a later run
    # proposing it again.
    for position, node_id in enumerate(frontier):
        note = selection.note_for(position)
        if note and note.verdict:
            trace.nodes[node_id].comparison = note.verdict

    if selection.undecided:
        return _undecided(state, selection, frontier)

    winner = selection.winner
    for position, node_id in enumerate(frontier):
        if position != winner:
            trace.set_status(node_id, "pruned",
                             trace.nodes[node_id].comparison or selection.reasoning)
    trace.set_status(frontier[winner], "kept", selection.reasoning)
    trace.score(frontier[winner], 1.0, selection.reasoning)

    rejected = list(state.get("rejected_notes") or []) + [
        f"{trace.nodes[frontier[p]].title or f'branch {p}'}: "
        f"{trace.nodes[frontier[p]].comparison or 'not chosen'}"
        for p in range(len(frontier)) if p != winner
    ]
    return {"path": state["path"] + [frontier[winner]], "rejected_notes": rejected}


def _undecided(state: CreativeThinkerState, selection: BranchSelection,
               frontier: list[str]) -> dict:
    """The selector could not choose: stop, keep everything, hand the choice up.

    The run asks *one question with the branches as options* and proposes nothing.
    It used to also raise a grouped ``create-variant`` per branch — an offer to make
    a variant of each — which asked the same thing twice in two vocabularies and put
    proposal cards on a run that had merely failed to decide.
    """
    trace: TraceBuilder = state["trace"]
    for node_id in frontier:
        trace.attach_question(node_id, "Which branch should this idea follow?")
        trace.set_status(node_id, "kept", trace.nodes[node_id].comparison or selection.reasoning)
    trace.note_escalation("undecided", frontier[0], selection.reasoning)

    return {
        "status": "stopped-uncertain",
        "status_reason": (selection.reasoning
                          or "the evaluator could not separate the leading branches"),
        "questions": list(state.get("questions") or []) + [OpenQuestion(
            node_id=frontier[0],
            question=("The branches at this depth look equally promising — which one "
                      "should the idea follow?"),
            options=[trace.nodes[n].title or trace.nodes[n].text[:160] for n in frontier],
        )],
        "frontier": [],
    }


def should_continue(state: CreativeThinkerState) -> str:
    """Deepen, or stop. ``depth`` is the ceiling the plan asked for; a stop is never
    an error.

    Reaching the requested ``depth`` ends the run ``completed`` — the plan asked for a
    tree of that depth and got one. A ceiling the plan did *not* ask for (budget,
    time) ends it ``truncated``; an undecided selection ends it ``stopped-uncertain``.
    """
    if state.get("status") in ("truncated", "stopped-uncertain"):
        return "writeback"
    if not state.get("frontier"):
        # The expansion proposed nothing, or the round was dropped unevaluated.
        return "writeback"
    if state.get("depth", 0) >= max(1, state["spec"].depth):
        return "writeback"
    if not state["ledger"].can_spend_llm():
        return "writeback"
    return "expand"


def writeback(state: CreativeThinkerState) -> dict:
    """Write the surviving reasoning back into the idea copy's content.

    Refinement refines: this rewrites the description and the sections of the
    **one** idea it was given, and produces the answer to the task. Those two plus
    the run entry are the whole output — a refinement run returns no drafts.

    The single exception is the one thing the run cannot decide for itself: it did
    what it was asked and, in doing so, moved the record far enough that overwriting
    the original would lose an idea. Then the *same* rewritten copy is offered as a
    ``create-variant`` with the overwrite still on the table, and the user picks.
    """
    spec: ThinkerCall = state["spec"]
    ledger: BudgetLedger = state["ledger"]
    style = style_roster.refinement_style(spec.styles)
    path = state.get("path") or []
    status = state.get("status", "completed")
    drafts = list(state.get("drafts") or [])

    answer = ""
    proposed_changes: list[ProposedChange] = []
    if ledger.can_spend_llm():
        # Called even when no branch survived: the run still owes an answer to the task
        # it was set, and "nothing could be settled, here is why" is a real answer. It
        # is the write-up of *what happened*, not a reward for having succeeded.
        edit = apply_edits(style=style, idea_md=state["idea_md"], task=spec.task,
                           path_md=_path_md(state) if len(path) > 1 else "",
                           findings="\n".join(state.get("findings") or []))
        ledger.spend_llm()
        answer = edit.answer.strip()
        if len(path) > 1:
            before = _record_fingerprint(state["idea"])
            merge_edit(state["idea"], edit)
            touched = _record_fingerprint(state["idea"]) != before
            state["trace"].set_status(path[-1], "accepted", edit.change_summary)
            # A run that answered a question and correctly changed nothing still did
            # its job, and the summary has to say which of the two happened —
            # "refinement applied" over a run that applied nothing is a lie the next
            # run reads as fact. Measured on the record, not on what was returned:
            # a section rewritten to the same words changed nothing.
            change = edit.change_summary or (
                "refinement applied" if touched
                else "the task was answered; the record needed no change"
            )
        else:
            change = "no branch survived — the idea copy is unchanged apart from its trace"
        open_questions = list(edit.open_questions)
        operations = _divergence(state, edit)
        proposed_changes = list(edit.proposed_changes or [])
    else:
        change = "budget exhausted before the write-up — the idea copy is unchanged"
        open_questions = []
        operations = []

    reason = state.get("status_reason") or (
        f"reached the requested depth {spec.depth}" if status == "completed" else ""
    )
    summary = RunSummary(
        objective=spec.task or f"develop {state['idea'].title or 'the idea'} further",
        accepted=change,
        rejected="; ".join(state.get("rejected_notes") or []),
        open=open_questions,
    )
    return {"drafts": drafts, "summary": summary, "answer": answer,
            "operations": operations, "proposed_changes": proposed_changes,
            "status": status, "status_reason": reason}


def _record_fingerprint(idea: IdeaRecord) -> tuple:
    """The parts of an idea a human would be accepting, as a comparable value.

    ``runs[]`` is excluded: the trace is appended to every copy, so including it
    would make every run look like a content change — the same exclusion the app
    makes when it decides whether a run earned an accept button.

    The two repairs :func:`merge_edit` performs on every write-up are normalized
    away rather than counted: it backfills an empty ``problem`` from the one-liner
    and writes ``section_order`` down when the record arrived without one. Both fix
    a record that was incomplete; neither is this run changing the idea, and
    counting them would make *every* run report an edit — which is precisely the
    claim this function exists to stop being made falsely.
    """
    return (idea.description,
            idea.problem or (idea.one_liner or "").strip(),
            idea.sections,
            idea.section_order or list(idea.sections or {}))



def _divergence(state: CreativeThinkerState, edit: IdeaEdit) -> list[SuggestedOperation]:
    """The run's one possible suggestion: *this may no longer be the same idea*.

    Raised only when the write-up says so, and only for a record that has somewhere
    to diverge **from** — an idea with no id was not yet in the workspace, so there
    is no original to keep beside it.

    The payload carries no separate record on purpose. The variant *is* the copy in
    ``envelope.idea``; what the two options differ on is where it lands — a new idea
    with an edge back to the parent, or over the parent itself.
    """
    if not edit.diverged or not state["idea"].idea_id:
        return []
    path = state.get("path") or []
    return [SuggestedOperation(
        type="create-variant",
        node_id=path[-1] if path else "",
        diagnosis=(edit.divergence_reason
                   or "carrying out this task changed what the idea is"),
        rationale=edit.change_summary,
        payload={
            "title": edit.variant_title or f"{state['idea'].title} (variant)",
            "parent_idea_id": state["idea"].idea_id,
            # The two readings of the same result, named for the UI: keep both
            # records, or let the rewrite replace the one it came from.
            "options": ["create-variant", "overwrite"],
        },
    )]


def _open_questions(idea: IdeaRecord) -> list[str]:
    """The idea's stated open questions — the branch points refinement uses."""
    raw = (idea.sections or {}).get("open_questions")
    if isinstance(raw, list):
        return [str(q) for q in raw]
    if isinstance(raw, str) and raw.strip():
        return [line.lstrip("-* ").strip() for line in raw.splitlines() if line.strip()]
    return []


# --- assemble ----------------------------------------------------------------


def assemble(state: CreativeThinkerState) -> dict:
    """Append the run to ``runs[]`` and build the envelope. The only exit."""
    envelope = build_envelope(
        idea=state["idea"],
        trace=state["trace"],
        ledger=state["ledger"],
        status=state.get("status", "completed"),
        reason=state.get("status_reason", ""),
        drafts=state.get("drafts"),
        operations=state.get("operations"),
        proposed_changes=state.get("proposed_changes"),
        questions=state.get("questions"),
        summary=state.get("summary") or None,
        answer=state.get("answer", ""),
    )
    log_event("run assembled", status=envelope.status, drafts=len(envelope.drafts),
              operations=len(envelope.suggested_operations),
              questions=len(envelope.questions), nodes=envelope.metrics.nodes)
    # JSON, not the model: this delta is what the run log records and what the
    # harness returns, so the state that leaves the graph is exactly what a
    # replayed run reads back.
    return {"result": envelope.model_dump(mode="json")}


# --- graph -------------------------------------------------------------------


def build_refine_subgraph():
    """The tree-of-thoughts loop, mounted as one node of the main graph.

    ``expand → evaluate ⟲ expand`` until the depth or a ceiling stops it, then
    ``writeback``. ``n = 1`` runs it once and degenerates to a chain of thought
    through exactly this code path.
    """
    g = StateGraph(CreativeThinkerState)
    g.add_node("expand", expand)
    g.add_node("evaluate", evaluate)
    g.add_node("writeback", writeback)

    g.add_edge(START, "expand")
    g.add_edge("expand", "evaluate")
    g.add_conditional_edges("evaluate", should_continue,
                            {"expand": "expand", "writeback": "writeback"})
    g.add_edge("writeback", END)
    return g.compile()


def build_creative_thinker_graph():
    """prepare → (generate | refine) → assemble → END."""
    g = StateGraph(CreativeThinkerState)
    g.add_node("prepare", prepare)
    g.add_node("generate", generate)
    g.add_node("refine", build_refine_subgraph())
    g.add_node("assemble", assemble)

    g.add_edge(START, "prepare")
    g.add_conditional_edges("prepare", route,
                            {"generate": "generate", "refine": "refine"})
    g.add_edge("generate", "assemble")
    g.add_edge("refine", "assemble")
    g.add_edge("assemble", END)
    return g.compile()


_GRAPH = None


def _get_graph():
    global _GRAPH
    if _GRAPH is None:
        _GRAPH = build_creative_thinker_graph()
    return _GRAPH


# --- entry points ------------------------------------------------------------


def think(spec: ThinkerCall, *, investigator: Callable[[str], dict] | None = None) -> Envelope:
    """The orchestrator's callable interface. **Pure**: no file is written.

    ``spec.idea`` is a snapshot — it is deep-copied here and never mutated, so
    the caller's record is byte-identical after the call. Everything the run
    produced is in the returned :class:`Envelope`; what becomes global is the
    orchestrator's decision.
    """
    final = _get_graph().invoke({"spec": spec, "investigator": investigator})
    return Envelope.model_validate(final["result"])


def _load_json(value: str | None, base: Path) -> Any:
    """A fixture filename, a path, or inline JSON — whichever the harness passed."""
    if not value:
        return None
    text = str(value).strip()
    if text.startswith(("{", "[")):
        return json.loads(text)
    path = Path(text)
    if not path.is_absolute():
        path = base / text
    return json.loads(path.read_text(encoding="utf-8"))


def _load_idea(value: str | None) -> IdeaRecord:
    """The idea to think on: a fixture, inline JSON, or ``ws:<slug>/<idea_id>``.

    The ``ws:`` form reads a **real workspace record**, normalized on the way in — a
    Phase-1 idea has no ``runs[]``, and this is what lets a run give it its first one.
    Reading is all that happens here; writing anything back is
    :func:`src.agents.idea_agent.apply_envelope`, and only when asked.
    """
    text = str(value or "").strip()
    if text.startswith("ws:"):
        slug, _, idea_id = text[3:].partition("/")
        raw = storage.read_idea_json(slug, idea_id)
        if raw is None:
            raise FileNotFoundError(f"no idea {idea_id!r} in workspace {slug!r}")
        return normalize_idea(raw)
    return IdeaRecord.model_validate(_load_json(text, FIXTURES) or {})


FIXTURES = REPO_ROOT / "dev" / "fixtures"


def _shape(mode: str, *, width: Any = None, depth: Any = None,
           n: Any = None) -> dict[str, int]:
    """``width``/``depth`` for the call, honouring a legacy ``n``.

    Both harness entry points resolve the shape identically, so a UI run and a CLI
    run are the same run. An explicit ``width``/``depth`` always wins; ``n`` fills
    whichever axis its mode used it for, and generation is depth 1 by definition.
    """
    def number(value: Any, fallback: int) -> int:
        try:
            return max(1, int(value))
        except (TypeError, ValueError):
            return fallback

    legacy = number(n, 0) if n not in (None, "") else 0
    if mode == "refinement":
        return {"width": number(width, 3), "depth": number(depth, legacy or 3)}
    return {"width": number(width, legacy or 3), "depth": 1}

# The dev harness may wire a real Investigator so ``tool_use`` can be shown end to
# end. The wiring lives here, at the boundary — the thinker itself imports nothing
# from the Investigator and only ever sees a callable.
_INVESTIGATOR_ON = {"web", "investigator", "true", "1", "yes", "on"}
_INVESTIGATOR_SESSION = "dev_creative_thinker"


def _make_investigator(kind: str | None) -> Callable[[str], dict] | None:
    """``"web"`` → the investigator agent as a ``(question) -> {answer, sources}``.

    Note what this costs: the Investigator searches the web and stores what it
    downloads under ``workspace/dev_creative_thinker/``. Those are *its* writes at
    the harness boundary, not the thinker's — the thinker still returns everything
    it produced and persists nothing.
    """
    if str(kind or "").strip().lower() not in _INVESTIGATOR_ON:
        return None

    def ask(question: str) -> dict:
        # Imported on first use, not at wiring time: the investigator pulls in the PDF
        # stack, and a run that never asks a question should not pay for it.
        from src.agents.investigator_agent import run as run_investigator

        answer = run_investigator(f"search the web: {question}",
                                  session_id=_INVESTIGATOR_SESSION, stream=False)
        return {"answer": answer or "", "sources": []}

    return ask


def run(idea: str = "", mode: str = "generation", width: int = 3, depth: int = 3,
        task: str = "", styles: str = "", context: str = "", tool_use: bool = False,
        max_llm_calls: int = 60, triggering_action: str = "dev harness",
        investigator: str = "none", persist: str = "", stream: bool = True,
        echo: bool | None = None, n: int | None = None) -> dict:
    """Standalone / dev-harness entry point — same graph, recorded as a run log.

    ``idea`` and ``context`` are fixture filenames (in ``dev/fixtures/``), paths,
    or inline JSON. Reading them and writing the run's ``.jsonl`` are this
    function's only disk touches: **no workspace record is created or modified**,
    which is why the manifest declares no ``workspace_field`` and the log lands in
    the harness' own graph-log directory.

    ``n`` is accepted for fixtures and callers written before ``width``/``depth``
    split apart; it is read onto whichever axis its mode used it for.

    Returns the envelope as a JSON-ready dict.
    """
    resolved = "refinement" if str(mode).lower().startswith("r") else "generation"
    spec = ThinkerCall(
        mode=resolved,
        idea=_load_idea(idea),
        context=_load_json(context, FIXTURES) or {},
        task=str(task or ""),
        styles=[AgentStyle(name=s.strip()) for s in str(styles).split(",") if s.strip()],
        **_shape(resolved, width=width, depth=depth, n=n),
        tool_use=bool(tool_use),
        budget=Budget(max_llm_calls=int(max_llm_calls)),
        triggering_action=triggering_action,
        session_id=f"dev-{resolved}",
    )
    inputs = {"spec": spec, "investigator": _make_investigator(investigator)}
    graph = _get_graph()

    if not stream:
        return _persist(graph.invoke(inputs)["result"], persist)

    if echo is None:
        echo = sys.stderr.isatty()
    rl = GraphRunLog("creative_thinker", log_dir=REPO_ROOT / "dev" / "runs" / "graph_logs",
                     run_hint=f"{spec.mode}-{spec.idea.title or 'idea'}")
    rl.run(graph, inputs, echo=echo)
    if rl.error:
        raise RuntimeError(rl.error)
    return _persist(rl.output, persist)


def _persist(envelope: dict, slug: str) -> dict:
    """Optionally apply the envelope to a workspace — the **orchestrator's** step.

    Off by default, and never done by the agent: the thinker returned a modified copy
    and that is where its responsibility ends. With ``persist=<slug>`` the harness plays
    orchestrator and writes the copy back, which is what puts the run's thinking tree
    into the workspace ``idea.json``. Drafts and split suggestions are **not** applied —
    those need an explicit user accept, so they stay in the envelope.
    """
    if not slug or not envelope:
        return envelope
    from src.agents.idea_agent import apply_envelope

    applied = apply_envelope(slug, Envelope.model_validate(envelope))
    return {**envelope, "applied": applied}


def post_run(inputs: dict, output: dict) -> dict:
    """Harness hook: apply the envelope after a *streamed* run, if asked.

    The dev UI streams the graph directly rather than calling :func:`run`, so the
    orchestrator's persist step has to hang off the run rather than off the entry point.
    Same function either way, and still outside the graph: the agent has finished and
    returned before anything is written.
    """
    return _persist(output, str(inputs.get("persist") or ""))


def input_adapter(inputs: dict) -> dict:
    """Dev-UI form values → graph inputs.

    The UI can only send strings, and the graph takes a :class:`ThinkerCall`
    holding a whole ``idea.json``. This builds the spec the same way :func:`run`
    does, so a UI run and a CLI run are the same run.
    """
    resolved = ("refinement" if str(inputs.get("mode", "")).lower().startswith("r")
                else "generation")
    spec = ThinkerCall(
        mode=resolved,
        idea=_load_idea(inputs.get("idea")),
        context=_load_json(inputs.get("context"), FIXTURES) or {},
        task=str(inputs.get("task") or ""),
        styles=[AgentStyle(name=s.strip())
                for s in str(inputs.get("styles") or "").split(",") if s.strip()],
        **_shape(resolved, width=inputs.get("width"), depth=inputs.get("depth"),
                 n=inputs.get("n")),
        tool_use=str(inputs.get("tool_use", "")).strip().lower() in {"1", "true", "yes", "on"},
        budget=Budget(max_llm_calls=int(inputs.get("max_llm_calls") or 60)),
        triggering_action=str(inputs.get("triggering_action") or "dev UI"),
    )
    return {"spec": spec, "investigator": _make_investigator(inputs.get("investigator"))}


if __name__ == "__main__":
    # CLI: [mode] [width] [depth] [task] — runs against the bundled fixture idea.
    cli_mode = sys.argv[1] if len(sys.argv) > 1 else "generation"
    cli_width = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    cli_depth = int(sys.argv[3]) if len(sys.argv) > 3 else 3
    cli_task = sys.argv[4] if len(sys.argv) > 4 else ""
    envelope = run(idea="idea_sample.json", context="creative_thinker_context.json",
                   mode=cli_mode, width=cli_width, depth=cli_depth, task=cli_task)
    print(json.dumps(envelope, indent=2)[:4000])
