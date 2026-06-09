"""Smoke test: verify litellm + DeepSeek work."""
from preparation_agent.llm import chat

if __name__ == "__main__":
    print(chat([{"role": "user", "content": "Say 'novadraft online' and nothing else."}]))
