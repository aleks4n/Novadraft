# Task

Your task is to generate arXiv search queries that find papers which might solve or address a given research gap. You will be provided with a gap, its description, its canonical description, and possibly a list of keywords. Based on this input, generate three different types of queries that together cover as much relevant ground as possible.

# Query Structure

- Generate a plain keyword query for the gap (e.g. `anomaly detection sensor`).
- Replace each concept with the concept plus its close synonyms, OR'd together - since arXiv AND's the words by default, synonyms widen recall to papers that phrase the same idea differently: `(anomaly detection OR outlier detection) AND (sensor OR monitor)`
- Each concept is now a synonym group: terms within a group are OR'd, groups stay AND'd. Multi-word terms stay one phrase (quoted automatically).

# Three Query Types

Each query approaches the gap from a different angle, so together they catch papers the others would miss. Generate one of each.

- **Literal** — the gap's own terminology. Finds papers that describe the gap the same way you do.
- **Reframed** — the same underlying problem in a different subfield's vocabulary or mechanism. Finds solutions that exist under foreign terminology.
- **Broad** — the gap's field or core problem at its most general, as 1–2 short, common terms. Use a single high-level concept (optionally one more very general group), not the literal query with synonyms stripped out. Prefer short head terms over long phrases. This is your high-recall safety net: it should catch papers the literal and reframed queries are too specific to reach.

# The Anchor

Build one anchor group: 1–3 broad terms naming the field the gap lives in (e.g. `time series OR temporal data`), OR'd together. Take these from the provided keywords; infer the field yourself if none are given. AND this same anchor onto all three queries so every result stays in the right field.

# General Guidelines

- Drop vague evaluative words such as `limitation`, `challenge`, `gap`, `issue`. They appear in almost every paper and destroy precision. 
- You may use a different number of concept groups for each query — you are not required to use the same count every time. Each group adds an AND, which narrows the results: more groups mean higher precision and fewer hits, fewer groups widen recall. The search step should favor recall — later steps re-read and filter these results, so a missed paper is lost for good while a loosely-relevant one is harmless. Use 1–4 synonyms per group.
- Multi-word terms match only as an exact contiguous phrase: `"self-supervised contrastive learning"` matches only papers containing that exact string. Prefer shorter head terms (`contrastive learning`, `self-supervised`) that more abstracts actually contain.
