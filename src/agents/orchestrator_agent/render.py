"""What the LLM steps see — of the context that was loaded, and of what the tools returned.

`context_view` and `results_view` are the only two things a prompt is ever handed, so this
module decides what an LLM reads. Three rules hold in both:

1. **No raw `model_dump()` JSON.** Field names outweigh content and nothing reads well.
2. **Never cut at a character count.** Slicing a record at N chars removes something
   without saying so, and a step that judges a truncated view reports a shortfall that
   isn't there. Drop whole fields instead, by name, and say why.
3. **State counts outright.** Anything a step has to establish by counting entries, it can
   get wrong.

These two views feed the planner and reflect, which route and check structure. They are
not what the creative thinker reads: it receives the whole `Context` object, so nothing
left out here is withheld from the step that thinks about it.
"""

from __future__ import annotations

from src.common.schemas.context import Context
from src.common.schemas.gap import GapRecord
from src.common.schemas.idea import IdeaRecord
from src.agents.orchestrator_agent.schemas import ToolCall, ToolResult

_MAX_PAPERS = 3  # papers run ~6k chars each; see context_view for why only these are capped


def _idea_block(idea: IdeaRecord, with_id: bool = False) -> list[str]:
    """One idea, in full.

    Omits `runs` (the thinker's thought graph — unbounded, and no structural criterion can
    be stated over it), `evaluation`, `provenance`, `origin` and the timestamps.

    `sections` is rendered in reading order via `ordered_sections()`, because the thinker
    names its own section keys and their order is part of the record rather than an
    accident of dict insertion. A value may be markdown or a list.

    `with_id` separates the two callers. A committed idea the user selected has an id and
    is a real, referenceable thing. A fresh draft has none — `idea_id is None` is what
    marks it — and inventing one for display would put an id in front of a model that
    could echo it into a sentence reading as though something was saved.
    """
    if with_id and idea.idea_id:
        lines, pad = [f'- idea {idea.idea_id}: "{idea.title}"'], "    "
    else:
        lines, pad = [f'  · "{idea.title}"'], "      "
    for text in (idea.one_liner, idea.problem, idea.description):
        if text:
            lines.append(f"{pad}{text}")
    for key, value in idea.ordered_sections():
        if isinstance(value, list):
            lines.append(f"{pad}{key}:")
            for item in value:
                lines.append(f"{pad}  - {item}")
        elif value:
            lines.append(f"{pad}{key}: {value}")
    if idea.addresses:
        lines.append(f"{pad}addresses: {', '.join(idea.addresses)}")
    for relation in idea.relations:
        lines.append(f"{pad}{relation.type} {relation.target}"
                     + (f" — {relation.rationale}" if relation.rationale else ""))
    return lines


def _entry(item: object) -> str:
    """A list entry from the paper summary. Records like `{"name": ..., "task": ...}` are
    flattened rather than shown as JSON; plain strings pass through."""
    if isinstance(item, dict):
        return " · ".join(str(v) for v in item.values() if v)
    return str(item)


def _gap_block(gap: GapRecord) -> list[str]:
    """One gap: what it claims, the sentence in the paper it rests on, and whether it is
    still open.

    Rendered from `model_dump` so nested records are plain dicts and a schema addition
    shows up here rather than raising.

    Omits `status` (a gap in the planner's context is validated by definition),
    `extracted_at`, and all of `ranking`. The ranking scores are the ranking agent's
    ordering signal — an LLM shown `impact: 0.22` writes toward the number — and
    `feasibility_reasoning` is ~1150 chars of prose about compute budgets, which the
    planner does not weigh when choosing a tool. `verdict.reasoning` is omitted for the
    same reason in a different direction: it is a paragraph about which papers failed to
    address the gap, and that framing pulls plans toward the investigator. Everything left
    out here still reaches the thinker through the `Context` object.
    """
    g = gap.model_dump(mode="json")
    lines = [f"- gap {g['gap_id']}" + (f"  [{g['type']}]" if g.get("type") else "")]
    if g.get("title"):
        lines.append(f"    {g['title']}")
    if g.get("canonical_description"):
        lines.append(f"    canonical: {g['canonical_description']}")
    if g.get("description"):
        lines.append(f"    {g['description']}")
    for evidence in g.get("evidence") or []:
        where = " / ".join(str(x) for x in (evidence.get("paper_id"),
                                            evidence.get("section")) if x)
        if quote := (evidence.get("quote") or evidence.get("anchor")):
            lines.append(f'    evidence ({where}): "{quote}"')
    verdict = g.get("verdict") or {}
    if verdict.get("status"):
        confidence = verdict.get("confidence")
        lines.append(f"    verdict: {verdict['status']}"
                     + (f" (confidence {confidence})" if confidence is not None else ""))
    return lines


def _paper_block(paper: dict) -> list[str]:
    """One paper: what it is about and what it left open. Deliberately brief.

    The paper agent's full structured summary — what the UI calls the paper's "survey" —
    runs ~6k chars, and rendering it here costs plan accuracy: a wall of literature detail
    makes the planner plan literature work, and buries the rules it should be following.
    The thinker receives every paper record in full through the `Context` object, so the
    summary is not withheld from the step that reasons over it.
    """
    pid = paper.get("paper_id", "")
    year = paper.get("year")
    lines = [f"- paper {pid}" + (f" ({year})" if year else "")
             + f"  {paper.get('title') or pid}"]
    summary = paper.get("summary")
    if not isinstance(summary, dict):  # session papers are raw JSON of unknown shape
        return lines

    if keywords := (summary.get("keywords") or paper.get("keywords")):
        lines.append(f"    keywords: {', '.join(str(k) for k in keywords)}")
    if statement := (summary.get("problem") or {}).get("statement"):
        lines.append(f"    problem: {statement}")
    for thread in summary.get("open_threads") or []:
        lines.append(f"    open thread: {_entry(thread)}")
    return lines


def context_view(ctx: Context) -> str:
    """What was loaded for this turn, for the planner and for reflect."""
    lines: list[str] = []
    for gap in ctx.gaps:
        lines.extend(_gap_block(gap))
    for idea in ctx.ideas:
        lines.extend(_idea_block(idea, with_id=True))
    # Papers are capped, gaps and ideas are not: the user chose those, and dropping a
    # chosen record answers a different question than the one asked. Papers arrive
    # implicitly, one hop out from each gap. The cap announces itself.
    for paper in ctx.papers[:_MAX_PAPERS]:
        lines.extend(_paper_block(paper))
    if len(ctx.papers) > _MAX_PAPERS:
        rest = ctx.papers[_MAX_PAPERS:]
        ids = ", ".join(p.get("paper_id", "?") for p in rest)
        lines.append(f"- ({len(rest)} further paper(s) in context, not shown here: {ids}. "
                     f"Ask the investigator about one by id if it matters.)")
    for finding in ctx.findings:
        lines.append(f"- findings from an earlier step this turn:\n    {finding}")
    if ctx.missing_ids:
        lines.append(f"- (could not load: {', '.join(ctx.missing_ids)})")
    return "\n".join(lines) or "- (nothing selected)"


def history_view(history: list[dict]) -> str:
    """Earlier turns of this session, oldest first.

    One line each and nothing more. This is here so a follow-up resolves — "make the
    second one concrete" needs to know a previous turn offered candidates — not so the
    steps can reason over the transcript. The drafts themselves come back as objects in
    `Context.ideas`, which is what makes them workable rather than merely mentionable.
    """
    if not history:
        return "- (this is the first turn of the session)"
    lines = []
    for turn in history:
        lines.append(f"- user: {turn.get('message', '')}")
        lines.append(f"  you:  {turn.get('answer', '')}")
    return "\n".join(lines)


def _args_brief(step: ToolCall) -> str:
    """The step's arguments, with `instruction` pulled out onto its own line below."""
    bits = [f"{k}={v!r}" for k, v in step.args.items() if k != "instruction"]
    if step.needs:
        bits.append(f"needs={step.needs}")
    return ", ".join(bits)


def _block(result: ToolResult, step: ToolCall | None, label: str) -> list[str]:
    lines = [label]
    if step is not None:
        if brief := _args_brief(step):
            lines.append(f"    {brief}")
        if instruction := step.args.get("instruction"):
            lines.append(f"    instruction: {instruction}")
    if not result.ok:
        lines.append(f"    FAILED — {result.error}")
        return lines
    # A run that stops at a compute ceiling returns the survivors of its last completed
    # round. Saying so is what keeps reflect from reading a short result as poor thinking
    # and replanning into a budget that is already spent.
    if result.status and result.status != "completed":
        lines.append(f"    {result.status.upper()}"
                     + (f" — stopped by {result.limit}" if result.limit else "")
                     + ". This is what the step could afford, not all it had to say.")
    lines.append(f"    returned {len(result.ideas)} idea(s)")
    for idea in result.ideas:
        lines.extend(_idea_block(idea))
    if result.text:
        lines.append(f"    text: {result.text}")
    return lines


def results_view(steps: list[ToolCall], results: list[ToolResult], carried: int = 0) -> str:
    """What ran and what came back, for reflect to judge and for the planner to replan on.

    `carried` is how many of `results` were kept from earlier rounds — those have no step
    in this plan, so they are shown separately rather than mispaired by index.
    """
    if not results:
        return "- (no tools ran)"

    total = sum(len(r.ideas) for r in results)
    lines = [f"{total} candidate idea(s) in hand in total, across every step below.", ""]

    earlier, this_round = results[:carried], results[carried:]
    if earlier:
        lines.append(f"Kept from earlier rounds — already offered, do not produce again:")
        for result in earlier:
            lines.extend(_block(result, None, f"  {result.tool}"))
        lines += ["", "This round:"]

    for i, result in enumerate(this_round, 1):
        step = steps[i - 1] if i - 1 < len(steps) else None
        lines.extend(_block(result, step, f"step {i}  {result.tool}"))
    return "\n".join(lines)
