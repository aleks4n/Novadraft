from __future__ import annotations
import os
from dotenv import load_dotenv

load_dotenv()  # pick up DEEPSEEK_API_KEY / OPENAI_API_KEY from a repo-root .env

# role -> model id, per provider
_OPENAI: dict[str, str] = {"reasoner": "gpt-4o", "cheap": "gpt-4o-mini"}
_DEEPSEEK: dict[str, str] = {"reasoner": "deepseek-reasoner", "cheap": "deepseek-chat"}
_DEEPSEEK_BASE_URL = "https://api.deepseek.com"


def _build(role: str):
    """Construct a chat model for one role from whichever provider key is set."""
    from langchain.chat_models import init_chat_model

    if os.getenv("DEEPSEEK_API_KEY"):
        return init_chat_model(
            _DEEPSEEK[role],
            model_provider="openai",  # DeepSeek speaks the OpenAI API
            api_key=os.environ["DEEPSEEK_API_KEY"],
            base_url=_DEEPSEEK_BASE_URL,
            temperature=0,
        )
    if os.getenv("OPENAI_API_KEY"):
        return init_chat_model(_OPENAI[role], model_provider="openai", temperature=0)
    raise RuntimeError(
        "No model API key found. Set DEEPSEEK_API_KEY or OPENAI_API_KEY "
        "(e.g. in a repo-root .env file)."
    )


def get_llm(role: str):
    """Return a chat model for the given role ('reasoner' | 'cheap')."""
    llm = _build(role)
    if role == "reasoner":
        # Automatic failover: reasoner down -> retry on the cheap model.
        llm = llm.with_fallbacks([_build("cheap")])
    return llm
