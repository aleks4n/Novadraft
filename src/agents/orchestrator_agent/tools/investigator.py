"""The investigator as the orchestrator calls it.

Unlike the creative thinker this one is already real — `src/agents/investigator_agent`
takes a free-text instruction and returns plain text.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from src.common.schemas.context import Context
from src.agents.orchestrator_agent.schemas import ToolResult
from src.agents.orchestrator_agent.tools.base import Tool, ToolEnv


class InvestigatorArgs(BaseModel):
    query: str = Field(default="", description="What to find out, as a plain question.")
    mode: Literal["workspace", "paper", "paper_search", "web", "url"] | None = Field(
        default=None,
        description="Optional direct route. Set it when the required source type is "
                    "already clear so the investigator can skip its routing LLM.",
    )
    sources: list[str] = Field(
        default_factory=list,
        description="Exact arXiv IDs, DOI values, or URLs to fetch directly. Use this "
                    "for named papers instead of searching for them again.",
    )
    paper_count: int = Field(
        default=3, ge=1, le=5,
        description="Maximum papers to retrieve for paper_search.",
    )


def run(args: InvestigatorArgs, _ctx: Context, env: ToolEnv) -> ToolResult:
    """Call the investigator agent and return what it reported.

    The import is lazy: the agent pulls in the paper pipeline and the web stack, and this
    module is imported at dev-harness discovery time.

    Passing `category_slug` is what puts its downloads where the next turn will look:
    with it the investigator writes to `workspace/<category>/sessions/<session_id>/`,
    which is the directory `assemble_context` already reads session papers from. Omitting
    it selects the standalone legacy path, and fetched papers then reach nobody.
    """
    if not args.query.strip():
        return ToolResult(tool=TOOL.name, ok=False, error="empty query")

    from src.agents.investigator_agent import run as run_investigator

    text = run_investigator(args.query,
                            session_id=env.session_id or "session-adhoc",
                            category_slug=env.category_slug or None,
                            mode=args.mode,
                            sources=args.sources,
                            paper_count=args.paper_count,
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
