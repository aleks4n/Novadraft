from langchain_core.messages import AIMessage, ToolMessage
from pydantic import BaseModel

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


def test_structured_reask_includes_the_invalid_answer_and_correction(monkeypatch):
    class Result(BaseModel):
        value: str

    seen_messages = []

    class BoundLlm:
        def invoke(self, messages):
            seen_messages.append(list(messages))
            if len(seen_messages) == 1:
                return AIMessage(content="I think the value should be usable prose.")
            return AIMessage(content="", tool_calls=[{
                "name": "Result", "args": {"value": "structured"},
                "id": "call_1", "type": "tool_call",
            }])

    class Llm:
        def bind_tools(self, *_args, **_kwargs):
            return BoundLlm()

    monkeypatch.setattr(registry, "emit_trace", lambda _event: None)

    result = registry.chat_with_structured_output(Llm(), "system", "user", Result)

    assert result.value == "structured"
    assert len(seen_messages) == 2
    assert seen_messages[1][-2].content.startswith("I think")
    assert "could not be parsed as Result" in seen_messages[1][-1].content
    assert "Do not answer with prose" in seen_messages[1][-1].content


def test_structured_reask_acknowledges_malformed_tool_call(monkeypatch):
    class Result(BaseModel):
        value: str

    seen_messages = []

    class BoundLlm:
        def invoke(self, messages):
            seen_messages.append(list(messages))
            if len(seen_messages) == 1:
                return AIMessage(content="", invalid_tool_calls=[{
                    "name": "Result", "args": "{broken", "id": "bad_call",
                    "error": "invalid JSON", "type": "invalid_tool_call",
                }])
            return AIMessage(content="", tool_calls=[{
                "name": "Result", "args": {"value": "fixed"},
                "id": "good_call", "type": "tool_call",
            }])

    class Llm:
        def bind_tools(self, *_args, **_kwargs):
            return BoundLlm()

    monkeypatch.setattr(registry, "emit_trace", lambda _event: None)

    result = registry.chat_with_structured_output(Llm(), "system", "user", Result)

    assert result.value == "fixed"
    tool_messages = [message for message in seen_messages[1]
                     if isinstance(message, ToolMessage)]
    assert len(tool_messages) == 1
    assert tool_messages[0].tool_call_id == "bad_call"
    assert "Invalid Result arguments" in tool_messages[0].content
