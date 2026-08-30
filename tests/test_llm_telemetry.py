from langchain_core.messages import AIMessage

from src.llm import registry


class RateLimited(Exception):
    status_code = 429


def test_chat_emits_logical_call_provider_attempt_and_retry_events(monkeypatch):
    events = []
    attempts = 0

    class FlakyLlm:
        def invoke(self, _messages):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RateLimited("slow down")
            return AIMessage(content="done")

    monkeypatch.setattr(registry, "emit_trace", events.append)
    monkeypatch.setattr(registry.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(registry.random, "random", lambda: 0.0)

    assert registry.chat(FlakyLlm(), "system", "user") == "done"

    names = [event["name"] for event in events]
    assert names.count("llm call started") == 1
    assert names.count("llm provider attempt started") == 2
    assert names.count("llm provider attempt failed") == 1
    assert names.count("llm retry scheduled") == 1
    assert names.count("llm provider attempt finished") == 1
    assert names.count("llm call finished") == 1

    call_ids = {
        event["data"]["call_id"] for event in events
        if event["name"].startswith("llm ") and "call_id" in event["data"]
    }
    assert len(call_ids) == 1
