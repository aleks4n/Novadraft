"""Idea agent — the Phase 2–3 half of the system.

Designed as `plan → execute → reflect` around two workers: the **investigator**
(convergent, ReAct) and the **creative thinker** (divergent, tree of thoughts). Only the
creative thinker is implemented so far, in `src.agents.creative_thinker`.

What lives here today is the one orchestrator responsibility the thinker cannot take on
without breaking its own isolation: :mod:`src.agents.idea_agent.apply`, which persists a
result envelope into the workspace after an explicit accept. No `MANIFEST` — this is not
yet a runnable agent, so the dev registry skips it.
"""

from .apply import apply_envelope, new_idea_id, promote_variant

__all__ = ["apply_envelope", "new_idea_id", "promote_variant"]
