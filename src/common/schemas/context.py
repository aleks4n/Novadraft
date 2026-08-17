"""The context bundle — what an agent is allowed to look at for one turn.

Assembled deterministically by the orchestrator from what the user selected in the UI,
then passed to whichever subagent runs. One schema for everyone: the orchestrator builds
it, the creative thinker and investigator read it.

Records that fail validation are skipped and named in `missing_ids` rather than raising —
a stale id in the UI, or a file written by an earlier prototype, shouldn't kill a turn.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from src.common.schemas.gap import GapRecord
from src.common.schemas.idea import Idea


class Context(BaseModel):
    gaps: list[GapRecord] = Field(default_factory=list)
    ideas: list[Idea] = Field(default_factory=list)
    papers: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Paper records in scope — the session's fetches plus any the user named.",
    )
    missing_ids: list[str] = Field(
        default_factory=list, description="Focus ids that could not be loaded."
    )
