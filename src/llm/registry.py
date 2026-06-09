"""Central model registry.

Agents ask for a *role* ("reasoner", "drafter"), not a model name. Swapping a
model — or repointing a role from OpenAI to DeepSeek — is a one-line edit here,
and no agent code changes.

If no API key is set we fall back to a tiny mock chat model so the demo runs
end-to-end with zero setup. Delete `_MockChat` and the fallback branch once you
wire in real keys.
"""

from __future__ import annotations

import os

# role -> "provider:model" string understood by init_chat_model
MODELS: dict[str, str] = {
    "reasoner": "openai:gpt-4o",
    "drafter": "deepseek:deepseek-chat",
    "cheap": "openai:gpt-4o-mini",
}


def _has_real_key() -> bool:
    return bool(os.getenv("OPENAI_API_KEY") or os.getenv("DEEPSEEK_API_KEY"))


def get_llm(role: str):
    """Return a chat model for the given role.

    Falls back to a deterministic mock when no API key is configured so the
    demo is runnable out of the box.
    """
    if not _has_real_key():
        return _MockChat(role)

    from langchain.chat_models import init_chat_model

    llm = init_chat_model(MODELS[role])
    # Automatic failover: reasoner down -> retry on the cheap model.
    if role == "reasoner":
        llm = llm.with_fallbacks([init_chat_model(MODELS["cheap"])])
    return llm


class _MockChat:
    """Minimal stand-in for a LangChain chat model (no API key needed)."""

    def __init__(self, role: str):
        self.role = role

    def invoke(self, messages):
        from langchain_core.messages import AIMessage

        # Echo a deterministic, structured-ish reply so downstream code works.
        last = messages[-1][1] if isinstance(messages[-1], tuple) else messages[-1].content
        return AIMessage(content=f"[mock:{self.role}] {last[:80]}")
