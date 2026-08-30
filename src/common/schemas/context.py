"""The context bundle — what an agent is allowed to look at for one turn.

Assembled deterministically by the orchestrator from what the user selected in the UI,
then passed to whichever subagent runs. One schema for everyone: the orchestrator builds
it, the creative thinker and investigator read it.

Records that fail validation are skipped and named in `missing_ids` rather than raising —
a stale id in the UI, or a file written by an earlier prototype, shouldn't kill a turn.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from src.common.schemas.gap import GapRecord
from src.common.schemas.idea import IdeaRecord


class UserInput(BaseModel):
    """Something the user put into the session themselves, rather than something the
    workspace holds.

    Two ways it arrives — a note pasted into the chat, or a document dropped onto it —
    told apart by `kind` because a dropped file is referable by name ("what does the
    Chen paper say") while a note is not, and because a note is short by construction
    and a document is not.

    This is *not* the user's message for this turn. That reaches the planner as
    `message` and the thinker as the planner's `instruction`; repeating it here would
    put a second paraphrase of the same request in front of the thinker, competing with
    the instruction it was actually given.
    """

    id: str = Field(default="", description="The file's name, or the note's id.")
    title: str = ""
    kind: Literal["note", "file"] = "note"
    text: str = Field(default="", description="The content as text — or, when `extracted` "
                                              "is false, a line saying why there is none.")
    extracted: bool = Field(
        default=True,
        description="False for a document whose text nothing could read. Stated rather "
                    "than inferred from an empty `text`, because an unread document and "
                    "an empty one are not the same thing and a step that cannot tell them "
                    "apart will write about a file it never saw.",
    )


class Context(BaseModel):
    gaps: list[GapRecord] = Field(default_factory=list)
    ideas: list[IdeaRecord] = Field(default_factory=list)
    papers: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Paper records in scope — the session's fetches plus any the user named.",
    )
    user_input: list[UserInput] = Field(
        default_factory=list,
        description="Notes the user pasted and documents they dropped into this session. "
                    "Nothing in the workspace holds a copy, so this is the only way they "
                    "reach a step.",
    )
    findings: list[str] = Field(
        default_factory=list,
        description="What an earlier step in this turn reported back — the investigator's "
                    "prose, carried into a later step that declared it as a dependency.",
    )
    catalog: dict[str, Any] = Field(
        default_factory=dict,
        description="The rest of the workspace as an index — id, title and one marker per "
                    "record, for everything NOT loaded above. This is what a step reads to "
                    "learn that something exists; the record behind an entry is fetched by "
                    "asking the investigator for it by id. Built by "
                    "`src.common.tools.catalog.build`.",
    )
    missing_ids: list[str] = Field(
        default_factory=list, description="Focus ids that could not be loaded."
    )
