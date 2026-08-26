# What happens on one turn

Every scenario the orchestrator can take, recorded from the code rather than
described alongside it. Each block is the real call chain from a real run through
the graph, with the two LLM steps and the two subagents recorded; the arguments
shown are the ones they were actually handed.

Regenerate with `python -m dev.record_flows`. The same scenarios are asserted in
`tests/test_orchestrator_flows.py`, so a flow cannot change here without either
changing there or failing.

---
## Conversational turn calls no tool and skips reflect

*"what can you do?" — the planner answers, and execute and reflect never run.*

The reply comes from the planner rather than reflect because on a turn with no steps there are no results to reflect on, and the planner is the only step that sees the tool catalogue.

```
1. orchestrator → **plan** (LLM)
```

## Generation offers drafts and writes nothing

*A gap is selected and the user asks for candidates.*

The thinker is handed the gap in `context.gaps` and no subject — generation has none. What comes back is offered, and the idea graph is untouched until an accept.

```
1. orchestrator → **plan** (LLM)
2. orchestrator → **creative thinker**
      mode: 'generation'
      styles: ['skeptic', 'transferer']
      width: 2
      depth: 3
      budget_llm_calls: 120
      context_gaps: ['gap_testflow']
      task: 'Fan out over the gap.'
3. orchestrator → **reflect** (LLM)
```

## Refinement hands the thinker exactly one subject

*Two ideas are in scope and the user means one of them.*

`work_on` names it; the adapter moves that record into `spec.idea` and leaves the other in `context.ideas` as background. That is what makes the thinker's contract — "refinement deepens ONE existing idea" — true rather than aspirational.

```
1. orchestrator → **plan** (LLM)
2. orchestrator → **creative thinker**
      mode: 'refinement'
      subject: 'idea_testbbb'
      subject_title: 'Low-rank bilinear form'
      styles: ['neutral']
      width: 3
      depth: 3
      budget_llm_calls: 120
      context_gaps: ['gap_testflow']
      context_ideas: ['idea_testaaa']
      task: 'Make it concrete.'
3. orchestrator → **reflect** (LLM)
```

## Work on naming an id not in context fails the step

*An id the planner invented never reaches the subagent.*

Without the check the thinker would silently work whichever idea came first, which is the failure the argument exists to prevent.

```
1. orchestrator → **plan** (LLM)
2. orchestrator → **reflect** (LLM)
```

## Chained steps pass ideas forward

*`generate -> refine(needs=[1])`.*

Step 2 works on what step 1 produced. Without `needs` it would run against the up-front context, find no ideas, and refine nothing.

```
1. orchestrator → **plan** (LLM)
2. orchestrator → **creative thinker**
      mode: 'generation'
      styles: ['skeptic', 'optimizer']
      width: 2
      depth: 3
      budget_llm_calls: 60
      context_gaps: ['gap_testflow']
3. orchestrator → **creative thinker**
      mode: 'refinement'
      subject_title: 'From skeptic'
      styles: ['neutral']
      width: 3
      depth: 3
      budget_llm_calls: 112
      context_gaps: ['gap_testflow']
      context_ideas: [None]
      task: 'Sharpen these.'
4. orchestrator → **reflect** (LLM)
```

## A refined draft replaces the one it rewrote

*`generate(2) -> refine`, with no idea named.*

Refinement returns its subject *rewritten*, not an extra idea, so the original stops being something to offer. Without this the user is shown the same idea twice — before and after — with nothing saying which supersedes which, and accepting both writes one idea to the graph as two records.

What is left still has to point at the right record inside its envelope: dropping the first candidate must not slide the second one's index up by one.

```
1. orchestrator → **plan** (LLM)
2. orchestrator → **creative thinker**
      mode: 'generation'
      width: 2
      depth: 3
      budget_llm_calls: 60
      context_gaps: ['gap_testflow']
3. orchestrator → **creative thinker**
      mode: 'refinement'
      subject_title: 'Alpha'
      styles: ['neutral']
      width: 3
      depth: 3
      budget_llm_calls: 112
      context_gaps: ['gap_testflow']
      context_ideas: [None]
4. orchestrator → **reflect** (LLM)
```

## Refining each candidate by step reference

*`generate(3) -> refine 1#1 -> refine 1#2 -> refine 1#3`.*

A candidate that does not exist yet has no id to name it by, so `work_on` also takes a `step#position` handle — knowable when the plan is written, because the plan is what decides how many the step returns.

Without it every refinement step falls back to the first idea in context and all three sharpen the same candidate, leaving the other two untouched while reporting success.

```
1. orchestrator → **plan** (LLM)
2. orchestrator → **creative thinker**
      mode: 'generation'
      width: 3
      depth: 3
      budget_llm_calls: 30
      context_gaps: ['gap_testflow']
3. orchestrator → **creative thinker**
      mode: 'refinement'
      subject_title: 'Alpha'
      styles: ['neutral']
      width: 3
      depth: 3
      budget_llm_calls: 37
      context_gaps: ['gap_testflow']
      context_ideas: [None, None]
4. orchestrator → **creative thinker**
      mode: 'refinement'
      subject_title: 'Beta'
      styles: ['neutral']
      width: 3
      depth: 3
      budget_llm_calls: 52
      context_gaps: ['gap_testflow']
      context_ideas: [None, None]
5. orchestrator → **creative thinker**
      mode: 'refinement'
      subject_title: 'Gamma'
      styles: ['neutral']
      width: 3
      depth: 3
      budget_llm_calls: 96
      context_gaps: ['gap_testflow']
      context_ideas: [None, None]
6. orchestrator → **reflect** (LLM)
```

## A step whose needs produced nothing is skipped not run

*A dependency that returned nothing stops the step rather than feeding it nothing.*

Reflect is told why, so it can replan instead of reporting a shortfall it cannot explain.

```
1. orchestrator → **plan** (LLM)
2. orchestrator → **creative thinker**
      mode: 'generation'
      width: 2
      depth: 3
      budget_llm_calls: 60
      context_gaps: ['gap_testflow']
3. orchestrator → **reflect** (LLM)
```

## Investigator findings flow into a later step

*`investigate -> generate(needs=[1])`.*

The investigator returns prose, not ideas, so it arrives as `context.findings` rather than replacing the subject. Research adds to what is known; it does not displace it.

```
1. orchestrator → **plan** (LLM)
2. orchestrator → **investigator**
      query: 'attention compatibility functions'
      session_id: 's1'
      category_slug: 'flowtest'
3. orchestrator → **creative thinker**
      mode: 'generation'
      width: 2
      depth: 3
      budget_llm_calls: 60
      context_gaps: ['gap_testflow']
      context_findings: ['Three papers tried bilinear forms; none beat dot product at scale.']
4. orchestrator → **reflect** (LLM)
```

## Replan keeping results accumulates instead of repeating

*Reflect finds the round short and sends it back with feedback.*

`keep_results=True` carries the first round forward, so the second plan adds the remainder and the user is not shown the same candidates twice.

```
1. orchestrator → **plan** (LLM)
2. orchestrator → **creative thinker**
      mode: 'generation'
      width: 3
      depth: 3
      budget_llm_calls: 120
      context_gaps: ['gap_testflow']
3. orchestrator → **reflect** (LLM)
4. orchestrator → **plan** (LLM)
5. orchestrator → **creative thinker**
      mode: 'generation'
      width: 1
      depth: 3
      budget_llm_calls: 112
      context_gaps: ['gap_testflow']
6. orchestrator → **reflect** (LLM)
```

## Replan discarding starts the round clean

*`keep_results=False` throws the round away rather than adding to it.*

```
1. orchestrator → **plan** (LLM)
2. orchestrator → **creative thinker**
      mode: 'generation'
      width: 2
      depth: 3
      budget_llm_calls: 120
      context_gaps: ['gap_testflow']
3. orchestrator → **reflect** (LLM)
4. orchestrator → **plan** (LLM)
5. orchestrator → **creative thinker**
      mode: 'generation'
      width: 2
      depth: 3
      budget_llm_calls: 112
      context_gaps: ['gap_testflow']
6. orchestrator → **reflect** (LLM)
```

## A truncated run is reported as truncated not as a shortfall

*Hitting a compute ceiling is the normal way divergent thinking ends.*

The status reaches reflect so it can tell a budget-capped run apart from thinking that simply went badly — otherwise it replans into a budget that is already spent.

```
1. orchestrator → **plan** (LLM)
2. orchestrator → **creative thinker**
      mode: 'generation'
      width: 5
      depth: 3
      budget_llm_calls: 120
      context_gaps: ['gap_testflow']
3. orchestrator → **reflect** (LLM)
```

## The reflect loop stops at the iteration cap

*A reflect that keeps asking to replan is overridden, and its answer is what shows.*

```
1. orchestrator → **plan** (LLM)
2. orchestrator → **creative thinker**
      mode: 'generation'
      width: 1
      depth: 3
      budget_llm_calls: 120
      context_gaps: ['gap_testflow']
3. orchestrator → **reflect** (LLM)
4. orchestrator → **plan** (LLM)
5. orchestrator → **creative thinker**
      mode: 'generation'
      width: 1
      depth: 3
      budget_llm_calls: 112
      context_gaps: ['gap_testflow']
6. orchestrator → **reflect** (LLM)
7. orchestrator → **plan** (LLM)
8. orchestrator → **creative thinker**
      mode: 'generation'
      width: 1
      depth: 3
      budget_llm_calls: 104
      context_gaps: ['gap_testflow']
9. orchestrator → **reflect** (LLM)
```

## Accepting a draft mints an id and carries the trace

*The accept gate — the only path from a draft to the idea graph.*

A draft has no `idea_id` until this call; the thinking graph lives on the envelope's carrier record, so the accept step replays the real envelope to carve the branch that produced the accepted candidate onto it.

```
1. orchestrator → **plan** (LLM)
2. orchestrator → **creative thinker**
      mode: 'generation'
      width: 2
      depth: 3
      budget_llm_calls: 120
      context_gaps: ['gap_testflow']
3. orchestrator → **reflect** (LLM)
```

## Accepting something the run never offered writes nothing

*The rule that survives from the orchestrator's own commit path.*

```
1. orchestrator → **plan** (LLM)
2. orchestrator → **creative thinker**
      mode: 'generation'
      width: 1
      depth: 3
      budget_llm_calls: 120
      context_gaps: ['gap_testflow']
3. orchestrator → **reflect** (LLM)
```

## A pasted note and a dropped document reach the thinker

*The user pasted a constraint and dropped a file into the session earlier.*

Neither is a workspace record, so neither can be loaded by id — they exist only in the session — and without reading them the turn would think about the gap while ignoring what the user actually put in front of it.

The two are shown differently on purpose. The note reaches the planner in full, because "one GPU" is an instruction that changes what a good plan is. The document does not: its text goes to the thinker through the context object, while the planner is told only that it is there. A document pasted into a planning prompt buries the rules the planner is following.

A PDF is a third case: nothing extracts its text yet, so it arrives saying so rather than arriving empty, and no step can mistake an unread document for an empty one.

```
1. orchestrator → **plan** (LLM)
2. orchestrator → **creative thinker**
      mode: 'generation'
      width: 1
      depth: 3
      budget_llm_calls: 120
      context_gaps: ['gap_testflow']
      context_user_input: ['note_0', 'constraints.md', 'chen2024.pdf']
      task: 'Respect the single-GPU constraint.'
3. orchestrator → **reflect** (LLM)
```

## A second turn sees the first turns drafts and transcript

*A session remembers, so a follow-up can refer to what just happened.*

The previous turn's drafts come back as objects in `context.ideas` — they were never committed, so there is no id to load them by — and the exchange comes back as history.

```
1. orchestrator → **plan** (LLM)
2. orchestrator → **creative thinker**
      mode: 'generation'
      width: 2
      depth: 3
      budget_llm_calls: 120
      context_gaps: ['gap_testflow']
3. orchestrator → **reflect** (LLM)
4. orchestrator → **plan** (LLM)
5. orchestrator → **creative thinker**
      mode: 'refinement'
      subject_title: 'First candidate'
      styles: ['neutral']
      width: 3
      depth: 3
      budget_llm_calls: 120
      context_gaps: ['gap_testflow']
      context_ideas: [None]
      task: 'Make the second concrete.'
6. orchestrator → **reflect** (LLM)
```

## A turn that has spent its budget stops instead of replanning

*Reflect asks for another round, and there is nothing left to run it with.*

Replanning here would spend a planning call to produce a step that can only truncate. The turn ends on the budget the way it ends on the iteration cap, and reflect's answer is what the user sees.

```
1. orchestrator → **plan** (LLM)
2. orchestrator → **creative thinker**
      mode: 'generation'
      width: 3
      depth: 3
      budget_llm_calls: 120
      context_gaps: ['gap_testflow']
3. orchestrator → **reflect** (LLM)
```

## The turn budget is divided across the steps it plans

*The thinker's ceiling is per call; a turn may hold several. The orchestrator slices.*

Without this a two-step plan over three reflect rounds can spend six times the ceiling anyone intended.

```
1. orchestrator → **plan** (LLM)
2. orchestrator → **creative thinker**
      mode: 'generation'
      width: 2
      depth: 3
      budget_llm_calls: 60
      context_gaps: ['gap_testflow']
3. orchestrator → **creative thinker**
      mode: 'refinement'
      subject_title: 'One'
      styles: ['neutral']
      width: 3
      depth: 3
      budget_llm_calls: 112
      context_gaps: ['gap_testflow']
      context_ideas: [None]
4. orchestrator → **reflect** (LLM)
```
