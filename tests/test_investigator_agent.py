from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.agents.investigator_agent import agent


def test_decide_request_routes_paper_query(monkeypatch):
	decision = SimpleNamespace(
		action="paper",
		should_fetch_pages=False,
		query="Attention is All You Need",
		url="",
		save_requested=True,
		reason="paper request",
	)

	monkeypatch.setattr(agent, "chat_with_structured_output", lambda *args, **kwargs: decision)
	monkeypatch.setattr(agent, "get_llm", lambda *args, **kwargs: object())

	result = agent._decide_request("search and download the paper: attention is all you need")

	assert result["mode"] == "paper"
	assert result["query"] == "Attention is All You Need"
	assert result["save_requested"] is True
	assert result["should_fetch_pages"] is False
	assert result["decision_reason"] == "paper request"


def test_decide_request_routes_web_query(monkeypatch):
	decision = SimpleNamespace(
		action="web",
		should_fetch_pages=False,
		query="effectiveness of A* algorithm",
		url="",
		save_requested=False,
		reason="web search",
	)

	monkeypatch.setattr(agent, "chat_with_structured_output", lambda *args, **kwargs: decision)
	monkeypatch.setattr(agent, "get_llm", lambda *args, **kwargs: object())

	result = agent._decide_request("search the web for effectiveness of a star algorithm on this problem")

	assert result["mode"] == "web"
	assert result["query"] == "effectiveness of A* algorithm"
	assert result["save_requested"] is False
	assert result["should_fetch_pages"] is False
	assert result["decision_reason"] == "web search"


def test_decide_request_routes_url_query(monkeypatch):
	decision = SimpleNamespace(
		action="url",
		should_fetch_pages=False,
		query="",
		url="https://example.com/report.pdf",
		save_requested=True,
		reason="direct url",
	)

	monkeypatch.setattr(agent, "chat_with_structured_output", lambda *args, **kwargs: decision)
	monkeypatch.setattr(agent, "get_llm", lambda *args, **kwargs: object())

	result = agent._decide_request("download this PDF: https://example.com/report.pdf")

	assert result["mode"] == "url"
	assert result["url"] == "https://example.com/report.pdf"
	assert result["query"] == "https://example.com/report.pdf"
	assert result["save_requested"] is True
	assert result["should_fetch_pages"] is False
	assert result["decision_reason"] == "direct url"


def test_decide_request_falls_back_when_llm_fails(monkeypatch):
	def _raise(*args, **kwargs):
		raise RuntimeError("offline")

	monkeypatch.setattr(agent, "chat_with_structured_output", _raise)

	result = agent._decide_request("search and download the paper: attention is all you need")

	assert result["mode"] == "paper"
	assert result["query"] == "attention is all you need"
	assert result["should_fetch_pages"] is False
	assert result["decision_reason"] == "heuristic fallback"


def test_web_node_fetches_pages_only_when_llm_requests_it(monkeypatch):
	search_results = [
		agent.SearchResult(title="One", url="https://example.com/1", snippet="s1"),
		agent.SearchResult(title="Two", url="https://example.com/2", snippet="s2"),
	]
	fetched = []

	monkeypatch.setattr(agent, "_search_web", lambda query: search_results)
	monkeypatch.setattr(
		agent,
		"_scrape_url",
		lambda url, session_id, save_requested=False: fetched.append((url, session_id, save_requested)) or {"kind": "html", "url": url, "saved_to": None, "title": url, "text": "body"},
	)
	answer_inputs = []
	monkeypatch.setattr(
		agent,
		"_answer_from_fetched_pages",
		lambda query, pages: answer_inputs.append((query, pages)) or "Grounded answer",
	)

	no_fetch = agent.web_node({"query": "topic", "text": "topic", "session_id": "session-1", "should_fetch_pages": False})
	assert no_fetch["research_result"]["pages"] == []
	assert fetched == []

	with_fetch = agent.web_node({"query": "topic", "text": "topic", "session_id": "session-1", "should_fetch_pages": True})
	assert len(with_fetch["research_result"]["pages"]) == 2
	assert fetched == [("https://example.com/1", "session-1", False), ("https://example.com/2", "session-1", False)]
	assert answer_inputs == [("topic", with_fetch["research_result"]["pages"])]
	assert with_fetch["research_result"]["answer"] == "Grounded answer"


def test_answer_from_fetched_pages_sends_query_and_content_to_deepseek(monkeypatch):
	request = {}

	monkeypatch.setattr(agent, "get_llm", lambda role, temperature: (role, temperature))
	monkeypatch.setattr(
		agent,
		"chat",
		lambda llm, system, user: request.update(llm=llm, system=system, user=user) or " Answer from pages ",
	)

	answer = agent._answer_from_fetched_pages(
		"What happened?",
		[{"url": "https://example.com/source", "title": "Source", "text": "The event happened yesterday."}],
	)

	assert answer == "Answer from pages"
	assert request["llm"] == ("cheap", 0)
	assert "What happened?" in request["user"]
	assert "The event happened yesterday." in request["user"]
	assert "https://example.com/source" in request["user"]


def test_paper_node_uses_session_workspace(monkeypatch):
	created = []
	called = []
	monkeypatch.setattr(agent, "_ensure_workspace", lambda session_id: created.append(session_id))
	monkeypatch.setattr(
		agent,
		"run_paper_agent",
		lambda session_id, query, stream: called.append((session_id, query, stream)) or {"status": "ok"},
	)

	result = agent.paper_node({"session_id": "session-42", "query": "Attention Is All You Need"})

	assert created == ["session-42"]
	assert called == [("session-42", "Attention Is All You Need", False)]
	assert result == {"research_result": {"status": "ok"}}


def test_renderers_format_text_output():
	paper_text = agent._paper_to_text(
		{
			"status": "ok",
			"fetch": {
				"paper": {
					"paper_id": "arxiv_1706.03762",
					"metadata": {"title": "Attention Is All You Need", "authors": ["Vaswani et al."], "year": 2017},
					"source": {"resolved_via": "arxiv", "source_url": "https://arxiv.org/abs/1706.03762"},
					"warnings": [],
				},
			},
			"summary": {"summary": "Transformer summary."},
			"extract": {"status": "ok"},
			"gaps": {"gap_ids": ["g1"]},
		},
		"attention is all you need",
	)

	assert "Paper ID: arxiv_1706.03762" in paper_text
	assert "Title: Attention Is All You Need" in paper_text
	assert "Summary:" in paper_text
	assert "Transformer summary." in paper_text

	web_text = agent._web_to_text(
		{
			"search_results": [{"title": "Example", "url": "https://example.com", "snippet": "Snippet"}],
			"pages": [{"title": "Example", "url": "https://example.com", "saved_to": None, "text": "Body text"}],
		},
		"example query",
	)

	assert "Web research" in web_text
	assert "Example" in web_text
	assert "Body text" in web_text
	assert agent._web_to_text({"answer": "Grounded answer"}, "example query") == "Grounded answer"

	url_text = agent._url_to_text(
		{"kind": "pdf", "saved_to": "/tmp/example.pdf", "title": "report", "text": "PDF body"},
		"https://example.com/report.pdf",
	)

	assert "URL scrape" in url_text
	assert "Saved to: /tmp/example.pdf" in url_text
	assert "PDF body" in url_text


def test_run_uses_injected_graph(monkeypatch):
	class FakeGraph:
		def __init__(self):
			self.inputs = None

		def invoke(self, inputs):
			self.inputs = inputs
			return {"result": "ok"}

	fake_graph = FakeGraph()
	monkeypatch.setattr(agent, "_get_graph", lambda: fake_graph)

	result = agent.run("search the web for transformers", session_id="session-123", stream=False)

	assert result == "ok"
	assert fake_graph.inputs == {"text": "search the web for transformers", "session_id": "session-123"}


def test_run_rejects_unsafe_session_id(monkeypatch):
	monkeypatch.setattr(agent, "_get_graph", lambda: None)

	with pytest.raises(ValueError, match="session_id"):
		agent.run("search the web", session_id="../outside", stream=False)
