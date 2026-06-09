"""Prompt construction for the GapAgent (Stage 4).

Single-paper, grounded extraction. We pick the sections most likely to contain
stated gaps by keyword-matching on the section names actually present in the
paper (Stage 2 doesn't always normalize to canonical names — e.g. the GAN
paper has `advantages_and_disadvantages` and `conclusions_and_future_work`).
The agent can then call `get_section` to pull any other extracted section by
name if it has reason to think a gap is stated there.
"""
from __future__ import annotations

# Keywords that flag a section name as likely gap-bearing. Matched
# case-insensitively as substrings of the section key.
GAP_SECTION_HINTS = [
    "limitation",
    "disadvantag",      # "disadvantage(s)", "advantages_and_disadvantages"
    "discussion",
    "future_work",
    "future work",
    "conclusion",
    "open_problem",
    "open problem",
    "threat",           # "threats_to_validity"
    "caveat",
    "weakness",
]

# Char budget per inlined section.
DEFAULT_SECTION_CHAR_LIMIT = 6000

GAP_TYPES = [
    "limitation",
    "future_work",
    "caveat",
    "negative_result",
    "open_problem",
    "assumption",
    "threat_to_validity",
]


def select_gap_sections(section_names: list[str]) -> list[str]:
    """Pick gap-bearing sections from whatever names Stage 2 produced."""
    out: list[str] = []
    for name in section_names:
        lname = name.lower()
        if any(hint in lname for hint in GAP_SECTION_HINTS):
            out.append(name)
    return out


def get_section_tool_spec(available_sections: list[str]) -> dict:
    """Tool spec — enum is the actual sections extracted for this paper."""
    return {
        "type": "function",
        "function": {
            "name": "get_section",
            "description": (
                "Fetch the verbatim text of one section of this paper. Use only "
                "when you have a concrete reason to suspect a stated gap lives "
                "outside the sections already provided in the prompt. Returns "
                "the section text, or 'not available' if the section name is "
                "unknown."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "enum": available_sections,
                        "description": "Section name as it appears in this paper.",
                    },
                    "reason": {
                        "type": "string",
                        "description": "One short sentence: why you need this section.",
                    },
                },
                "required": ["name", "reason"],
            },
        },
    }


SYSTEM_PROMPT = """You extract STATED research gaps from a single paper.

You have access to a `get_section` tool. The user message includes the
sections most likely to contain stated gaps (limitations, discussion, future
work, conclusions, advantages/disadvantages, threats to validity — whichever
the paper actually has). If — while reading those — you suspect a gap is
stated in another section (e.g. an assumption mentioned in a method section,
a negative result in experiments, a known limitation flagged in the
introduction or related work), call `get_section` to fetch it. Use the tool
sparingly: only when you have a concrete reason, not to browse.

Once you have everything you need, respond with the final JSON object and
do NOT call any more tools.

A "stated gap" is something the paper itself acknowledges:
- a limitation the authors admit
- a piece of future work they propose
- a caveat or threat to validity they flag
- a negative result
- an open problem they cite
- an assumption their approach explicitly relies on

Strict rules:
- Only extract gaps that are GROUNDED in the paper's text. Every gap MUST
  have a verbatim quote drawn from one of the sections you have read.
- Do NOT infer gaps the paper doesn't state. "The paper didn't test X" is only
  a gap if the authors themselves say so. Cross-paper reasoning and unstated-
  gap inference happen in a later stage — not here.
- Do NOT judge whether a gap is already solved, novel, or severe. Just extract.
- One gap per distinct issue. Don't split one limitation into many bullet
  points; don't merge two unrelated issues into one gap.
- Use the candidate seeds (from the summary stage) as starting points. If a
  seed is real, find a verbatim quote that backs it — either in the inlined
  sections or via `get_section`. Discard a seed only if no such quote exists
  anywhere in the paper.
- Output a single JSON object matching the schema. No prose outside the JSON.

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
    return (
        "Return JSON with this exact shape:\n\n"
        "{\n"
        '  "gaps": [\n'
        "    {\n"
        '      "title": string,                  // short noun phrase, <80 chars\n'
        '      "description": string,            // 1-3 sentences, self-contained\n'
        f'      "type": one of [{", ".join(GAP_TYPES)}],\n'
        '      "canonical_description": string,  // normalized dedup key\n'
        f'      "evidence_section": one of [{", ".join(available_sections)}],\n'
        '      "quote": string                   // verbatim span, <=300 chars\n'
        "    }\n"
        "  ]\n"
        "}\n\n"
        'If the paper states no gaps, return {"gaps": []}.\n'
    )


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


def build_user_prompt(title: str, abstract: str | None,
                      summary: dict,
                      all_sections: dict[str, str],
                      inline_section_names: list[str],
                      category: dict | None = None) -> str:
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
                     "below (or fetch another section via the tool). Discard a "
                     "seed only if no verbatim quote anywhere in the paper "
                     "backs it.\n\n" + "\n\n".join(seed_blocks))

    # All available section names (so the model knows what it can fetch)
    other = [n for n in all_sections.keys() if n not in inline_section_names]
    if other:
        parts.append("\n# Other sections in this paper (fetch via `get_section`)\n"
                     + ", ".join(other))

    # Inlined sections — the high-probability sources of quotes
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
        parts.append("\n(No section names matched the standard gap-bearing "
                     "keywords. Use the candidate seeds above and the "
                     "`get_section` tool to ground each one. The full list of "
                     "available sections is given just above.)")

    parts.append("\n---\n" + build_schema_instructions(list(all_sections.keys())))
    return "\n".join(parts)
