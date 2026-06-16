# Task

You are given a specific research gap and a numbered list of candidate papers (comprising titles and abstracts). Your task is to select the papers that are most likely to address, plausibly solve, or partially solve the described gap, so they can be prioritized for a full-text review. 

# Selection Criteria

- **Target the problem, not the topic**: The paper must explicitly tackel the specific problem described in the gap. Do not select papers merely because they share the same general subject area, keywords, or domain. 
- **Concrete over vague:** Judge relevance strictly using the provided title and abstract. Prefer abstracts that clearly and concretely engage with the gap over those that rely on vague or generalized statements.
- **Evidence:** Never invent or hallucinate relevance. The abstract must provide tangible evidence that the core problem of the gap is being addressed.

# Output Format & Constraints

- **Format:** Return only the chosen papers' positions (the bracketed numbers), ordered from **most relevant first**.
- **Limit:** Return at most {max_select} papers.
- **No Padding:** Return fewer than {max_select} if fewer are genuinely relevant. Do NOT pad the list to reach the maximum.

Pick the papers most likely to **address the gap** — that plausibly solve it, partially solve it,
or directly tackle the specific problem it describes — so they can be read in full
later.
