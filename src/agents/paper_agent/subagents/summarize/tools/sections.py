"""Section selection + truncation shared by the summarize extractor and critic.

The extractor (which produces the summary) and the critic (which judges it) must
see the same slice of the paper, so the canonical section order, the per-section
character budgets and the truncation/first-paragraph helpers live here as a tool
both import.
"""
from __future__ import annotations

# Canonical sections in priority order. `results` is truncated to first paragraph.
PRIORITY_SECTIONS = [
    "abstract",
    "introduction",
    "method",
    "results",
    "discussion",
    "limitations",
    "conclusion",
    "future_work",
]

# Char budget per section. This is the only stage that reads the paper's
# actual text (gap extraction re-reads it too, but summarize runs first and
# everything downstream — gap seeds, idea generation — traces back to what
# made it into the summary), so these are deliberately generous: a safety
# net against a pathological outlier section, not a routine cutoff. A real
# failure mode this caused at the old, tighter limits: `method` truncated at
# 8000 chars cut off a paper's positional-encoding subsection, so the model
# wrote about it from background knowledge while the critic flagged it as
# unsupported — neither side could see the part of the source that would
# have settled it.
PER_SECTION_CHARS = {
    "abstract": 6000,
    "introduction": 14000,
    "method": 24000,
    "results": 3000,        # first paragraph only
    "discussion": 10000,
    "limitations": 8000,
    "conclusion": 6000,
    "future_work": 6000,
}


def truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + " […truncated]"


def first_paragraph(text: str) -> str:
    for sep in ("\n\n", "\n"):
        idx = text.find(sep)
        if idx > 0:
            return text[:idx]
    return text
