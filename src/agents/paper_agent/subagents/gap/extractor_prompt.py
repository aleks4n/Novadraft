"""Prompt construction for the gap subagent's extractor (Stage 4).

Single-paper, grounded extraction. No tool calling: every section is inlined
except `abstract` and `related_work`, which are excluded by a fixed rule
(decided upfront, not by the model) — abstract is redundant with the summary
seeds already given, and related_work is about other papers' work, not gaps
this paper itself states. There is no escape hatch to fetch more text, so
omitting a section here means the agent simply never sees it.

The extractor runs for up to a few passes (see gap.MAX_GAP_LOOPS), each
judged by the critic (critic_prompt.py). Later passes get loop context —
gaps already found, gaps rejected by the critic, gaps that need a better
anchor, and hints about likely-missing gaps — so the model only does
incremental work instead of re-deriving everything.
"""
from __future__ import annotations

from .....common.schemas.gap import GAP_TYPES, LLMGapList

# Sections excluded from extraction by a fixed rule — not a model decision.
EXCLUDED_SECTIONS = {"abstract", "related_work", "related work"}

# Char budget per inlined section — a safety net against a pathological
# outlier section, not a routine cutoff. See summarize/extractor_prompt.py's
# PER_SECTION_CHARS comment: this agent re-reads the same raw sections, so
# it inherits the same truncation-hides-real-content risk.
DEFAULT_SECTION_CHAR_LIMIT = 20000


def select_gap_sections(section_names: list[str]) -> list[str]:
    """All sections Stage 2 extracted, minus the fixed exclusion list.

    Deliberately the "all except N" direction, not "search for the right
    N" — every section name actually present in this paper is included
    unless it matches EXCLUDED_SECTIONS by exact (case-insensitive) name.
    """
    return [n for n in section_names if n.strip().lower() not in EXCLUDED_SECTIONS]


SYSTEM_PROMPT = """You extract STATED research gaps from a single paper.

You are given every section of this paper except the abstract and related
work (redundant with material already provided, or about other papers, not
this one). Read everything given and respond with the final JSON object —
there is no tool to fetch more text, so if a gap isn't grounded in what you
were given, leave it out.

This call may be one of several passes over the same paper. The prompt may
include:
- "Gaps already found": do NOT re-propose any of these — only return gaps
  not already in that list.
- "Rejected" (checked and found invalid): do not propose these again.
- "Needs a better anchor": you're given the full prior gap (title,
  description, type, canonical_description, section, and the anchor that
  failed). Re-emit it EXACTLY as given, only with a corrected anchor — do
  not rewrite the title/description/type, the previous anchor just didn't
  match the section text uniquely.
- "Needs revision": you're given the full prior gap plus a reviewer's reason
  for flagging it (a debatable `type` or imprecise title/description — not
  a factual problem with the anchor). Re-emit it with the SAME
  canonical_description, section, and anchor, but revise title/description/
  type to address the reason given.
- "Possibly missing" hints from a reviewer: use them to look again, but
  only report a gap if you find real grounding for it in the given text.
If none of these are present, this is the first pass — extract everything
you can find.

A "stated gap" is something the paper itself acknowledges:
- a limitation the authors admit
- a piece of future work they propose
- a caveat or threat to validity they flag
- a negative result
- an open problem they cite
- an assumption their approach explicitly relies on

Strict rules:
- Only extract gaps that are GROUNDED in the paper's text. Every gap MUST
  have an `anchor` drawn from the sections you were given (see "About
  `anchor`" below) — not a paraphrase, not a quote you transcribe yourself.
- Do NOT infer gaps the paper doesn't state. "The paper didn't test X" is only
  a gap if the authors themselves say so. Cross-paper reasoning and unstated-
  gap inference happen in a later stage — not here.
- Do NOT judge whether a gap is already solved, novel, or severe. Just extract.
- One gap per distinct issue. Don't split one limitation into many bullet
  points; don't merge two unrelated issues into one gap.
- Use the candidate seeds (from the summary stage) as starting points. If a
  seed is real, find an anchor that backs it in the sections you were given.
  Discard a seed only if no such anchor exists in what you were given.
- Output a single JSON object matching the schema. No prose outside the JSON.

About `anchor`:
- Do NOT transcribe a quote yourself. A downstream deterministic step
  searches the section text for your `anchor` and expands it to its
  containing sentence to produce the stored quote — so the anchor only
  needs to pinpoint the right spot, not restate the claim.
- Give 3-10 consecutive words copied EXACTLY (same spelling, case, spacing)
  from the section text — character-for-character, not your gloss of it.
- The anchor must occur exactly once in the section. Pick words distinctive
  enough that they can't also appear elsewhere in the same section — avoid
  generic or repeated phrases ("the model", "future work", "our approach").
  If your first choice could plausibly repeat, lengthen it or shift it to a
  more unusual clause.
- Prefer plain prose words over mathematical notation. Sections are often
  TeX-extracted, so symbols you'd write as `p_g(x)` may appear in source as
  `$p_g(\\bm{x})$` — matching fails even though the claim is the same. Anchor
  on the surrounding prose instead (e.g. "the generator distribution" rather
  than the formula itself) unless the symbols themselves are the only
  distinctive part of the sentence.

About `canonical_description`:
- This is a normalized, paper-agnostic phrasing of the gap, used later as a
  dedup key against gaps from other papers.
- STRIP paper-specific names (model names, dataset names, the authors' own
  method names), paper-specific quantities, and hedging language ("we observe
  that...", "a limitation of our approach is...").
- KEEP the load-bearing concept.
- Examples:
    "Our FlashAttention-2 kernel only supports head dim <=128"
      -> "attention kernel limited to small head dimensions"
    "We did not evaluate on languages outside WMT14 En-De and En-Fr"
      -> "evaluation limited to high-resource european language pairs"
- Test: if a different paper described the same underlying issue, would your
  canonical_description for both come out roughly the same string?
"""


def build_schema_instructions(available_sections: list[str]) -> str:
    return LLMGapList.prompt_schema(available_sections)


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + " […truncated]"


def _format_seeds(label: str, items) -> str | None:
    if not items:
        return None
    bullets = "\n".join(f"- {x}" for x in items if x)
    if not bullets:
        return None
    return f"{label}:\n{bullets}"


def _format_loop_list(label: str, items: list[dict], reason_key: str | None = None) -> str | None:
    """Bullet a list of {"canonical_description": ..., maybe "reason": ...}."""
    if not items:
        return None
    bullets = []
    for it in items:
        canon = it.get("canonical_description") or it.get("title") or ""
        if not canon:
            continue
        if reason_key and it.get(reason_key):
            bullets.append(f"- {canon} ({it[reason_key]})")
        else:
            bullets.append(f"- {canon}")
    if not bullets:
        return None
    return f"{label}:\n" + "\n".join(bullets)


def _format_retry_anchor(items: list[dict]) -> str | None:
    """Full prior fields for each gap that just needs a better anchor — not
    just its canonical_description. Without this the model has to
    reconstruct title/description/type from a bare dedup string and ends up
    regenerating the gap instead of fixing the one thing that was wrong."""
    if not items:
        return None
    blocks = []
    for it in items:
        canon = it.get("canonical_description") or ""
        if not canon:
            continue
        blocks.append(
            f"- title: {it.get('title', '')}\n"
            f"  description: {it.get('description', '')}\n"
            f"  type: {it.get('type', '')}\n"
            f"  canonical_description: {canon}\n"
            f"  section: {it.get('section', '')}\n"
            f"  previous anchor (rejected): {it.get('anchor', '')!r}\n"
            f"  why it failed: {it.get('reason', '')}"
        )
    if not blocks:
        return None
    return ("Needs a better anchor — re-emit this EXACT gap (same title, "
           "description, type, canonical_description, section) with only the "
           "anchor corrected:\n" + "\n".join(blocks))


def _format_needs_revision(items: list[dict]) -> str | None:
    """Full prior fields for each gap a critic flagged a `minor` issue
    against — same idea as `_format_retry_anchor` but for content, not
    grounding: the model is asked to keep canonical_description/section/
    anchor unchanged and only fix title/description/type per `reason`."""
    if not items:
        return None
    blocks = []
    for it in items:
        canon = it.get("canonical_description") or ""
        if not canon:
            continue
        blocks.append(
            f"- title: {it.get('title', '')}\n"
            f"  description: {it.get('description', '')}\n"
            f"  type: {it.get('type', '')}\n"
            f"  canonical_description: {canon}\n"
            f"  section: {it.get('section', '')}\n"
            f"  anchor (keep unchanged): {it.get('anchor', '')!r}\n"
            f"  reviewer's reason: {it.get('reason', '')}"
        )
    if not blocks:
        return None
    return ("Needs revision — re-emit this gap with the SAME "
           "canonical_description, section, and anchor, fixing only "
           "title/description/type per the reviewer's reason:\n" + "\n".join(blocks))


def build_user_prompt(title: str, abstract: str | None,
                      summary: dict,
                      all_sections: dict[str, str],
                      inline_section_names: list[str],
                      category: dict | None = None,
                      already_found: list[dict] | None = None,
                      rejected: list[dict] | None = None,
                      retry_anchor: list[dict] | None = None,
                      needs_revision: list[dict] | None = None,
                      missing_hints: list[str] | None = None) -> str:
    parts: list[str] = []

    if category:
        cat_name = category.get("name") or category.get("category_slug") or ""
        cat_desc = category.get("description") or ""
        block = f"# Workspace category\nName: {cat_name}"
        if cat_desc:
            block += f"\nScope: {cat_desc}"
        block += ("\n\nUse the scope only for framing. Do NOT invent gaps to fit "
                  "the scope — only extract what the paper itself states.")
        parts.append(block + "\n")

    parts.append(f"# Paper\nTitle: {title}")
    if abstract:
        parts.append(f"Abstract: {_truncate(abstract, 3000)}")

    # Seeds from the summary stage
    seed_blocks = []
    for label, key_path in [
        ("Author-stated limitations", ("limitations_stated",)),
        ("Open threads / future work", ("open_threads",)),
        ("Approach assumptions", ("approach", "assumptions")),
        ("Prior-work limitations", ("problem", "prior_limitations")),
    ]:
        cur = summary
        for k in key_path:
            cur = (cur or {}).get(k) if isinstance(cur, dict) else None
        block = _format_seeds(label, cur if isinstance(cur, list) else None)
        if block:
            seed_blocks.append(block)
    if seed_blocks:
        parts.append("\n# Candidate seeds (from the summary stage)\n"
                     "Use as starting points, then ground each in the sections "
                     "below. Discard a seed only if no anchor in what you were "
                     "given backs it.\n\n" + "\n\n".join(seed_blocks))

    # Loop context — only present on 2nd+ passes.
    loop_blocks = [
        b for b in [
            _format_loop_list("Gaps already found (do not re-propose)", already_found or []),
            _format_loop_list("Rejected (checked and found invalid — do not propose again)",
                              rejected or [], reason_key="reason"),
            _format_retry_anchor(retry_anchor or []),
            _format_needs_revision(needs_revision or []),
        ] if b
    ]
    if missing_hints:
        loop_blocks.append("Possibly missing (a reviewer's hints — verify before reporting):\n"
                           + "\n".join(f"- {h}" for h in missing_hints))
    if loop_blocks:
        parts.append("\n# Loop context (this is a follow-up pass)\n" + "\n\n".join(loop_blocks))

    # Inlined sections — the only material this agent can see.
    parts.append("\n# Sections inlined for you")
    any_section = False
    for name in inline_section_names:
        text = all_sections.get(name)
        if not text:
            continue
        any_section = True
        text = _truncate(text, DEFAULT_SECTION_CHAR_LIMIT)
        parts.append(f"\n## {name}\n{text}")
    if not any_section:
        parts.append("\n(No sections available besides abstract/related_work. Use the "
                     "candidate seeds above only if you can ground them — otherwise "
                     "return an empty gaps list.)")

    parts.append("\n---\n" + build_schema_instructions(inline_section_names))
    return "\n".join(parts)
