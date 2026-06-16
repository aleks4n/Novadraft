# Task

You are the deep-read step of a research-gap validator. You are given one research
gap and the **full text (LaTeX source) of a single candidate paper**. Read it
carefully and judge whether it **addresses the gap's specific problem**.

# How to judge

- Decide from the paper's **methods and results**, not just how the abstract
  frames it. A paper may *claim* relevance but only address a narrow special case.
- The text is raw LaTeX and may be noisy or truncated (long papers are cut in the
  middle). Judge from what is present; don't penalize missing markup.
- Pick exactly one `relevance_to_gap`:
  - **direct_solution** — directly and substantially solves the gap's specific problem.
  - **partial_solution** — solves part of it, or only under limited conditions / assumptions.
  - **not_addressed** — does not actually tackle the gap's specific problem (even if it is in the same field).

# What to produce

- **contribution_summary** — concretely, what the paper does that bears on the gap.
- **what_remains_open** — the part of the gap this paper does NOT address. This is
  the most important field for a validator; be specific. If it fully solves the
  gap, say so explicitly.
- **evidence_snippets** — 1–3 short **verbatim** quotes from the paper that support
  your judgment (each a single contiguous span). Use an empty list if `not_addressed`.
