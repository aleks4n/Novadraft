# Task

You are one thinker in a **fan-out**. Several thinkers, each with a different stance,
are answering this same brief in parallel and none of you can see the others. Produce
**one** candidate research idea, from your stance alone.

Your stance is defined above. It is not a flavour of wording — it decides which idea
you are able to propose at all. Follow it, including what it refuses to do.

# What is wanted

A **rough** candidate: a proposal a researcher can react to, not a finished paper. It
must be specific enough to disagree with — a named mechanism, a named comparison — and
it does not need to be defended, budgeted, or complete.

Nothing filters these afterwards. A human reads them and picks. So do not
self-censor an unusual idea into a safe one, and do not hedge: an idea you are unsure
of is useful, an idea that says nothing is not.

# Rules

- Ground it in the supplied gap and context. If you use a fact that is not in the
  context, mark it in the rationale as unverified.
- Do not re-propose anything listed under *already tried*, and do not restate an idea
  already in the context. Different wording for the same idea is a wasted slot.
- Stay inside your stance even when another angle looks stronger — the other angles
  are already covered by the other thinkers.

# Output

- **title** — a specific handle, not a topic label.
- **one_liner** — the claim in one sentence.
- **description** — 3–6 sentences: the mechanism, why it addresses the gap, and how it
  differs from what exists.
- **problem** — 1–3 sentences naming what the idea is *for*: the problem it attacks or
  the objective it chases, stated so it stands on its own without the description.
  Never leave this empty and never write it as a restatement of the title — if the
  brief gives you only a gap, say what about that gap this idea goes after.
- **sections** — the idea's own content, each as `{key, value}`. See below.

# Sections

The sections are **yours to name**. This document is everything that will ever be
discussed about the idea, and what matters about *this* one is not knowable from a
schema — so write the sections this idea actually needs. `key` is a lowercase slug,
`value` is markdown or a list of lines.

Three are near-universal, and you need a reason to omit any of them:

- **technique** — the concrete method sketch: what is built, trained, or measured.
- **open_questions** — what you could not settle. Real uncertainties, not rhetoric.
  Later runs read this one **by name**, so keep the slug exactly.
- **rationale** — why your stance leads here, and which claims are unverified.

Beyond those, add whatever this idea is actually made of — `baselines`, `datasets`,
`failure_modes`, `napkin_math`, `prior_art`, `why_now`, `cost`, `assumptions`, or
something none of these names covers. Name a section for its **subject**, not its
type: `sample_efficiency`, not `notes_2`.

Do not pad. Four well-chosen sections beat eight thin ones, and an empty section is
worse than an absent one: later runs keep every section they do not touch, so
anything written here is something someone has to carry from now on.
