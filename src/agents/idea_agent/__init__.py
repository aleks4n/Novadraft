"""Idea agent — ``plan → execute → reflect`` around two workers: the investigator
(convergent, ReAct) and the creative thinker (divergent, tree of thoughts).

What lives here is :mod:`src.agents.idea_agent.apply`, which persists a result
envelope into the workspace after an explicit accept. There is no ``MANIFEST``, so
the registry does not expose this package as a runnable agent.
"""

from .apply import apply_envelope, new_idea_id, promote_variant

__all__ = ["apply_envelope", "new_idea_id", "promote_variant"]
