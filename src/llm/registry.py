from __future__ import annotations
import os

# role -> "provider:model" string understood by init_chat_model
MODELS: dict[str, str] = {
    "reasoner": "openai:gpt-4o",
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

