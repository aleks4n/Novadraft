"""The investigator as the orchestrator calls it.

Unlike the creative thinker this one is already real — `src/agents/investigator_agent`
takes a free-text instruction and returns plain text.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from src.common.schemas.context import Context
from src.agents.orchestrator_agent.schemas import ToolResult
from src.agents.orchestrator_agent.tools.base import Tool, ToolEnv


class InvestigatorArgs(BaseModel):
    query: str = Field(default="", description="What to find out, as a plain question.")


def run(args: InvestigatorArgs, _ctx: Context, env: ToolEnv) -> ToolResult:
    """Call the investigator agent and return what it reported.

    The import is lazy: the agent pulls in the paper pipeline and the web stack, and this
    module is imported at dev-harness discovery time.

    KNOWN MISMATCH — the investigator writes its downloads to `workspace/<session_id>/`,
    treating the session id as a category (it writes a meta.json there, so the session
    shows up in /api/topics as a research topic). This orchestrator keeps session files
    under `workspace/<category>/sessions/<session_id>/`. Until that is reconciled only the
    returned text is used, so papers it fetches do not reach the next turn's context.
    """
    if not args.query.strip():
        return ToolResult(tool=TOOL.name, ok=False, error="empty query")

    from src.agents.investigator_agent import run as run_investigator

    text = run_investigator(args.query, session_id=env.session_id or "session-adhoc",
                            stream=False)
    return ToolResult(tool=TOOL.name, text=str(text))


TOOL = Tool(
    name="investigator",
    description=(
        "Researches a question against real sources (literature, the workspace) and "
        "reports what it found, saving any papers it fetches into the session. Use it "
        "when the user asks to look something up, or when the answer needs evidence "
        "rather than thinking. It does not produce ideas."
    ),
    args_schema=InvestigatorArgs,
    fn=run,
)
