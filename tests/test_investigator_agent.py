from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.agents.investigator_agent import agent
from src.common.tools.paper_fetch.classify import classify
from src.common.utils import storage
from src.llm import registry


def test_direct_mode_skips_actor_llm_on_first_attempt(monkeypatch):
	monkeypatch.setattr(agent, "_ensure_workspace", lambda _key: None)
	monkeypatch.setattr(agent, "_load_reflections", lambda *_args: [])
	monkeypatch.setattr(
		agent, "_decide_request",
		lambda *_args: pytest.fail("direct mode should skip the routing LLM"),
	)

	result = agent.actor({
		"text": "compare these papers", "session_id": "session-1",
		"requested_mode": "paper_search", "paper_count": 2,
		"sources": ["2511.08544", "2603.19312"],
	})

	assert result["mode"] == "paper_search"
	assert result["attempt"] == 1


def test_paper_search_uses_direct_sources_without_search(monkeypatch):
	monkeypatch.setattr(
		agent, "_search_web", lambda *_args, **_kwargs: pytest.fail("must not search"),
	)
	result = agent.paper_search_node({
		"query": "compare", "paper_count": 2,
		"sources": ["2511.08544", "https://doi.org/10.1000/example"],
	})["research_result"]

	assert [item["url"] for item in result["search_results"]] == [
		"2511.08544", "https://doi.org/10.1000/example",
	]


def test_classifier_prefers_embedded_exact_identifier_over_title_search():
	assert classify("Bishop Bayesian PCA arXiv:physics/9905028").input_type == "arxiv_id"
	assert classify("Bishop Bayesian PCA arXiv:physics/9905028").value == "physics/9905028"
	assert classify("paper DOI:10.1162/089976699300016728").input_type == "doi"


def test_decide_request_routes_paper_query(monkeypatch):
	models = []
	decision = SimpleNamespace(
		action="paper",
		should_fetch_pages=False,
		query="Attention is All You Need",
		url="",
		save_requested=True,
		reason="paper request",
	)

	monkeypatch.setattr(agent, "chat_with_structured_output", lambda *args, **kwargs: decision)
	monkeypatch.setattr(agent, "get_llm", lambda role, **kwargs: models.append(role) or object())

	result = agent._decide_request("search and download the paper: attention is all you need")

	assert result["mode"] == "paper"
	assert result["query"] == "Attention is All You Need"
	assert result["save_requested"] is True
	assert result["should_fetch_pages"] is False
	assert result["decision_reason"] == "paper request"
	assert models == ["cheap"]


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


def test_decide_request_routes_multi_paper_download_to_search_workflow(monkeypatch):
	monkeypatch.setattr(agent, "chat_with_structured_output", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("offline")))

	result = agent._decide_request("download 3 papers about transformers")

	assert result["mode"] == "paper_search"
	assert result["query"] == "transformers"
	assert result["paper_count"] == 3


def test_multi_paper_search_deduplicates_mirrors_before_download():
	results = [
		agent.SearchResult("[1706.03762] Attention Is All You Need", "https://arxiv.org/abs/1706.03762"),
		agent.SearchResult("Attention is All You Need - Google Research", "https://research.google/pubs/attention-is-all-you-need/"),
		agent.SearchResult("arXiv:1706.03762v7", "https://arxiv.org/pdf/1706.03762"),
		agent.SearchResult("Attention Is All You Need - Wikipedia", "https://en.wikipedia.org/wiki/Attention_Is_All_You_Need"),
		agent.SearchResult("A Different Transformer Paper", "https://example.com/different"),
	]

	unique = agent._deduplicate_search_results(results)

	assert [item.url for item in unique] == [
		"https://arxiv.org/abs/1706.03762",
		"https://example.com/different",
	]


def test_multi_paper_workflow_searches_then_downloads_each_candidate(monkeypatch):
	candidates = [
		agent.SearchResult(title="Paper one", url="https://arxiv.org/abs/2401.00001"),
		agent.SearchResult(title="Paper two", url="https://arxiv.org/abs/2401.00002"),
		agent.SearchResult(title="Paper three", url="https://arxiv.org/abs/2401.00003"),
	]
	downloads = []
	monkeypatch.setattr(agent, "_search_web", lambda query, max_results=None: candidates)
	monkeypatch.setattr(agent, "_ensure_workspace", lambda session_id: None)
	monkeypatch.setattr(
		agent,
		"_fetch_extract_paper",
		lambda session_id, query: downloads.append((session_id, query)) or {"status": "ok", "paper_id": query, "full_text": "content"},
	)
	monkeypatch.setattr(agent, "_answer_from_papers", lambda question, papers: (
		"answer", [{"source_id": f"paper-{i}"} for i in range(1, 4)], [],
	))

	state = {"session_id": "session-42", "query": "transformers", "paper_count": 3}
	search_result = agent.paper_search_node(state)["research_result"]
	download_result = agent.download_papers_node({**state, "research_result": search_result})["research_result"]

	assert search_result["requested_count"] == 3
	assert sorted(downloads) == sorted([
		("session-42", "https://arxiv.org/abs/2401.00001"),
		("session-42", "https://arxiv.org/abs/2401.00002"),
		("session-42", "https://arxiv.org/abs/2401.00003"),
	])
	assert len(download_result["papers"]) == 3
	assert len(download_result["evidence"]) == 3
	assert len(download_result["attempted_papers"]) == 3


def test_paper_search_falls_back_to_arxiv_when_web_returns_empty(monkeypatch):
	monkeypatch.setattr(agent, "_search_web", lambda query, max_results=None: [])
	monkeypatch.setattr(
		agent,
		"_search_arxiv",
		lambda query, max_results=None: [
			agent.SearchResult(title="A", url="https://arxiv.org/abs/2401.00001"),
			agent.SearchResult(title="B", url="https://arxiv.org/abs/2401.00002"),
		],
	)

	result = agent.paper_search_node({"query": "transformers", "paper_count": 2})["research_result"]

	assert result["requested_count"] == 2
	assert [item["url"] for item in result["search_results"]] == [
		"https://arxiv.org/abs/2401.00001",
		"https://arxiv.org/abs/2401.00002",
	]


def test_multi_paper_workflow_uses_later_candidates_after_fetch_failures(monkeypatch):
	candidates = [
		agent.SearchResult("Blocked one", "https://arxiv.org/abs/2401.00001"),
		agent.SearchResult("Good one", "https://arxiv.org/abs/2401.00002"),
		agent.SearchResult("Blocked two", "https://arxiv.org/abs/2401.00003"),
		agent.SearchResult("Good two", "https://arxiv.org/abs/2401.00004"),
	]
	monkeypatch.setattr(agent, "_ensure_workspace", lambda session_id: None)
	runs = []
	monkeypatch.setattr(
		agent,
		"_fetch_extract_paper",
		lambda session_id, query: (
			runs.append((session_id, query))
			or {"status": "ok", "paper_id": query, "full_text": "content"}
		),
	)
	monkeypatch.setattr(agent, "_answer_from_papers", lambda question, papers: ("answer", [], []))
	state = {
		"session_id": "session-42", "query": "transformers", "paper_count": 2,
		"research_result": {"search_results": [item.__dict__ for item in candidates]},
	}

	result = agent.download_papers_node(state)["research_result"]

	assert [item["title"] for item in result["papers"]] == ["Blocked one", "Good one"]
	assert len(result["attempted_papers"]) == 2
	assert runs == [("session-42", "https://arxiv.org/abs/2401.00001"),
	                ("session-42", "https://arxiv.org/abs/2401.00002")]


def test_multi_paper_workflow_stops_after_requested_successes(monkeypatch):
	candidates = [
		agent.SearchResult(f"Paper {index}", f"https://arxiv.org/abs/2401.{index}")
		for index in range(10000, 10005)
	]
	runs = []
	monkeypatch.setattr(agent, "_ensure_workspace", lambda session_id: None)
	monkeypatch.setattr(
		agent,
		"_fetch_extract_paper",
		lambda session_id, query: runs.append((session_id, query)) or {
			"status": "ok",
			"paper_id": query,
			"full_text": "content",
		},
	)
	monkeypatch.setattr(agent, "_answer_from_papers", lambda question, papers: ("answer", [], []))

	result = agent.download_papers_node({
		"session_id": "session-42", "query": "transformers", "paper_count": 3,
		"research_result": {"search_results": [item.__dict__ for item in candidates]},
	})["research_result"]

	assert len(runs) == 3
	assert len(result["papers"]) == 3


def test_multi_paper_workflow_does_not_expand_attempt_pool_on_failures(monkeypatch):
	candidates = [
		agent.SearchResult(f"Paper {index}", f"https://arxiv.org/abs/2401.{index}")
		for index in range(10000, 10006)
	]
	runs = []
	monkeypatch.setattr(agent, "_ensure_workspace", lambda session_id: None)
	monkeypatch.setattr(
		agent,
		"_fetch_extract_paper",
		lambda session_id, query: runs.append((session_id, query)) or {
			"status": "error",
			"error": "blocked",
		},
	)
	monkeypatch.setattr(agent, "_answer_from_papers", lambda question, papers: (None, [], []))

	result = agent.download_papers_node({
		"session_id": "session-42", "query": "transformers", "paper_count": 3,
		"research_result": {"search_results": [item.__dict__ for item in candidates]},
	})["research_result"]

	assert len(runs) == 3
	assert len(result["attempted_papers"]) == 3
	assert len(result["papers"]) == 3


def test_web_node_fetches_pages_only_when_llm_requests_it(monkeypatch):
	search_results = [
		agent.SearchResult(title="One", url="https://example.com/1", snippet="s1"),
		agent.SearchResult(title="Two", url="https://example.com/2", snippet="s2"),
	]
	fetched = []

	monkeypatch.setattr(agent, "_search_web", lambda query, max_results=None: search_results)
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
	monkeypatch.setattr(agent, "_claims_from_answer", lambda answer, evidence: [])

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
		"_fetch_extract_paper",
		lambda session_id, query: called.append((session_id, query)) or {"status": "ok", "full_text": "paper body"},
	)
	monkeypatch.setattr(agent, "_answer_from_papers", lambda question, papers: ("grounded answer", [], []))

	result = agent.paper_node({"session_id": "session-42", "query": "Attention Is All You Need"})

	assert created == ["session-42"]
	assert called == [("session-42", "Attention Is All You Need")]
	assert result["research_result"]["status"] == "ok"
	assert result["research_result"]["answer"] == "grounded answer"
	assert result["research_result"]["evidence"] == []


def test_renderers_format_text_output():
	paper_text = agent._paper_to_text(
		{
			"status": "ok",
			"answer": "Transformer summary.",
			"fetch": {
				"paper": {
					"paper_id": "arxiv_1706.03762",
					"metadata": {"title": "Attention Is All You Need", "authors": ["Vaswani et al."], "year": 2017},
					"source": {"resolved_via": "arxiv", "source_url": "https://arxiv.org/abs/1706.03762"},
					"warnings": [],
				},
			},
			"extract": {"status": "ok"},
		},
		"attention is all you need",
	)

	assert paper_text == "Transformer summary."

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


def test_run_places_category_session_in_nested_workspace(monkeypatch):
	class FakeGraph:
		def __init__(self):
			self.inputs = None

		def invoke(self, inputs):
			self.inputs = inputs
			return {"result": "ok"}

	fake_graph = FakeGraph()
	monkeypatch.setattr(agent, "_get_graph", lambda: fake_graph)

	result = agent.run(
		"search the web for transformers",
		session_id="session-123",
		category_slug="machine-learning",
		stream=False,
	)

	assert result == "ok"
	assert fake_graph.inputs == {
		"text": "search the web for transformers",
		"session_id": "session-123",
		"workspace_key": "machine-learning/sessions/session-123",
		# The bare slug travels beside the session path: fetches write under
		# `workspace_key`, while the workspace route reads the category's own
		# gaps/papers/ideas, which are its siblings rather than its children.
		"category_slug": "machine-learning",
	}


def test_category_session_workspace_path_is_safe_and_nested():
	assert agent._session_workspace("session-123", "machine-learning") == "machine-learning/sessions/session-123"
	with pytest.raises(ValueError, match="category_slug"):
		agent._session_workspace("session-123", "../outside")


def test_ensure_workspace_creates_nested_category_session_directory(monkeypatch, tmp_path):
	monkeypatch.setenv("NOVADRAFT_WORKSPACE", str(tmp_path))
	workspace_key = agent._session_workspace("session-123", "machine-learning")

	created = agent._ensure_workspace(workspace_key)

	assert created == tmp_path / "machine-learning" / "sessions" / "session-123"
	assert (created / "meta.json").is_file()


def test_actor_conditions_routing_on_saved_reflections(monkeypatch):
	captured = {}
	monkeypatch.setattr(agent, "_ensure_workspace", lambda session_id: None)
	monkeypatch.setattr(agent, "_load_reflections", lambda session_id, text: ["Use the canonical paper URL."])
	monkeypatch.setattr(
		agent,
		"_decide_request",
		lambda text, reflections: captured.update(text=text, reflections=reflections) or {"mode": "web", "query": text},
	)

	result = agent.actor({"text": "find the paper", "session_id": "session-42"})

	assert result["attempt"] == 1
	assert result["reflections"] == ["Use the canonical paper URL."]
	assert captured["reflections"] == ["Use the canonical paper URL."]


def test_evaluate_uses_llm_when_available(monkeypatch):
	captured = {}
	models = []

	monkeypatch.setattr(agent, "ENABLE_DETERMINISTIC_FETCH_FALLBACK", False)
	monkeypatch.setattr(agent, "get_llm", lambda role, **kwargs: models.append(role) or object())
	monkeypatch.setattr(
		agent,
		"chat_with_structured_output",
		lambda *args, **kwargs: captured.update(prompt=kwargs.get("prompt") or args[2]) or SimpleNamespace(success=False, feedback="The page was too sparse.", reason="missing evidence"),
	)

	evaluation = agent.evaluate(
		{
			"mode": "web",
			"attempt": 1,
			"text": "find a summary of the paper",
			"query": "summary of the paper",
			"research_result": {"search_results": [], "pages": []},
		}
	)

	assert evaluation["evaluation"]["success"] is False
	assert evaluation["evaluation"]["feedback"] == "The page was too sparse."
	assert "summary of the paper" in captured["prompt"]
	assert models == ["cheap"]


def test_evaluator_routes_failed_attempt_to_reflection_then_stops_at_limit(monkeypatch):
	monkeypatch.setattr(agent, "ENABLE_DETERMINISTIC_FETCH_FALLBACK", False)
	failed = agent.evaluate(
		{"mode": "web", "attempt": 1, "max_attempts": 2, "research_result": {"error": "offline"}}
	)
	state = {"attempt": 1, "max_attempts": 2, **failed}

	assert failed["evaluation"]["success"] is False
	assert agent._route_after_evaluate(state) == "reflect"
	assert agent._route_after_evaluate({**state, "attempt": 2}) == "render"


def test_evaluator_gives_up_on_fetch_failure_when_enabled(monkeypatch):
	monkeypatch.setattr(agent, "ENABLE_DETERMINISTIC_FETCH_FALLBACK", True)

	result = agent.evaluate(
		{"mode": "paper", "attempt": 1, "research_result": {"status": "error", "error": "offline"}}
	)

	assert result["evaluation"]["success"] is True
	assert result["evaluation"]["reason"] == "deterministic fetch fallback"
	assert agent._route_after_evaluate({"attempt": 1, **result}) == "render"


def test_reflect_adds_and_persists_actionable_lesson(monkeypatch):
	saved = []
	monkeypatch.setattr(agent, "get_llm", lambda *args, **kwargs: object())
	monkeypatch.setattr(
		agent,
		"chat_with_structured_output",
		lambda *args, **kwargs: SimpleNamespace(lesson="Search for the official PDF instead of the failed landing page."),
	)
	monkeypatch.setattr(agent, "_store_reflection", lambda session_id, text, lesson: saved.append((session_id, text, lesson)))

	result = agent.reflect(
		{
			"text": "download that paper",
			"session_id": "session-42",
			"mode": "paper",
			"attempt": 1,
			"evaluation": {"success": False, "feedback": "The paper workflow returned no source."},
		}
	)

	assert result["reflections"] == ["Search for the official PDF instead of the failed landing page."]
	assert saved == [("session-42", "download that paper", "Search for the official PDF instead of the failed landing page.")]


def test_run_rejects_unsafe_session_id(monkeypatch):
	monkeypatch.setattr(agent, "_get_graph", lambda: None)

	with pytest.raises(ValueError, match="session_id"):
		agent.run("search the web", session_id="../outside", stream=False)


def test_structured_output_normalizes_provider_envelopes():
	assert registry._normalize_structured_args(
		{"critique": '{"status": "pass"}'}, agent.InvestigatorEvaluation
	) == {"status": "pass"}
	assert registry._normalize_structured_args(
		{"info": {"success": True}}, agent.InvestigatorEvaluation
	) == {"success": True}


def test_web_node_attaches_evidence_and_citation_claims(monkeypatch):
	monkeypatch.setattr(
		agent, "_search_web",
		lambda query: [agent.SearchResult("Source", "https://example.com", "snippet")],
	)
	monkeypatch.setattr(
		agent, "_scrape_url",
		lambda url, session_id, save_requested=False: {
			"kind": "html", "url": url, "title": "Source", "text": "Evidence text",
		},
	)
	monkeypatch.setattr(agent, "_answer_from_fetched_pages", lambda query, pages: "Answer")
	monkeypatch.setattr(
		agent, "_claims_from_answer",
		lambda answer, evidence: [{"text": answer, "source_ids": ["page-1"], "confidence": "high", "verified": True}],
	)

	result = agent.web_node({
		"query": "question", "session_id": "session-1", "should_fetch_pages": True,
	})["research_result"]

	assert [item["source_id"] for item in result["evidence"]] == ["web-1", "page-1"]
	assert result["claims"][0]["source_ids"] == ["page-1"]
	assert "Citations:" in agent._web_to_text(result, "question")


# --- workspace route ---------------------------------------------------------
# The investigator's fourth source: what the project already holds. These cover the
# routing decision, the lookup itself, and the one case that used to be silent — a run
# with no category attached, which has no workspace to read and must say so rather than
# answering from nothing.


def test_decide_request_routes_workspace_lookup(monkeypatch):
	decision = SimpleNamespace(
		action="workspace",
		workspace_ids=["gap_04e60ada"],
		section="",
		should_fetch_pages=False,
		query="what does this gap say",
		url="",
		save_requested=False,
		reason="the request names a gap id",
	)
	monkeypatch.setattr(agent, "chat_with_structured_output", lambda *a, **k: decision)
	monkeypatch.setattr(agent, "get_llm", lambda *a, **k: object())

	result = agent._decide_request("what does gap_04e60ada say?")

	assert result["mode"] == "workspace"
	assert result["workspace_ids"] == ["gap_04e60ada"]
	assert result["save_requested"] is False


def test_heuristic_routes_record_ids_to_workspace():
	"""No LLM needed: an id names something on disk, and no web search will find it."""
	result = agent._heuristic_decide_request("summarise gap_04e60ada and arxiv_1706.03762")

	assert result["mode"] == "workspace"
	assert result["workspace_ids"] == ["gap_04e60ada", "arxiv_1706.03762"]


def test_workspace_node_without_category_reports_rather_than_inventing(monkeypatch):
	"""A standalone run has no category, so there is nothing of the project's to read.

	It must fail loudly: the evaluator reads this as an unusable attempt and sends the
	actor to the web, where a silent empty answer would have ended the run instead.
	"""
	state = {"text": "what gaps do we have", "query": "gaps", "mode": "workspace",
			 "session_id": "s1"}

	out = agent.workspace_node(state)

	assert "error" in out["research_result"]
	assert not out["research_result"].get("answer")


def test_workspace_lookup_reads_gaps_papers_and_reports_unknown_ids(tmp_path, monkeypatch):
	import json
	from src.agents.investigator_agent import workspace_lookup

	monkeypatch.setattr(storage, "workspace_root", lambda: tmp_path)
	category = tmp_path / "cat"
	(category / "gaps" / "master").mkdir(parents=True)
	(category / "papers" / "arxiv_1").mkdir(parents=True)
	(category / "meta.json").write_text("{}")
	(category / "gaps" / "master" / "gap_a.json").write_text(json.dumps({
		"gap_id": "gap_a", "title": "Attention is quadratic", "type": "limitation",
		"description": "cost grows with sequence length",
		"evidence": [{"paper_id": "arxiv_1", "section": "results", "quote": "it is quadratic"}],
	}))
	(category / "papers" / "arxiv_1" / "paper.json").write_text(json.dumps({
		"paper_id": "arxiv_1", "metadata": {"title": "A Paper", "year": 2017},
		"content": {"sections": {"results": "the results section body"}},
	}))

	found = workspace_lookup.lookup("cat", ids=["gap_a", "gap_missing"])
	assert [r["id"] for r in found["records"]] == ["gap_a"]
	assert found["unknown_ids"] == ["gap_missing"]
	assert "it is quadratic" in found["text"]  # the evidence quote, not just the claim

	# A query finds the same gap by its words, without anyone knowing its id.
	searched = workspace_lookup.lookup("cat", query="quadratic attention cost")
	assert "gap_a" in [r["id"] for r in searched["records"]]

	# A named section is rendered; the summary alone would not carry it.
	sectioned = workspace_lookup.lookup("cat", ids=["arxiv_1"], section="results")
	assert "the results section body" in sectioned["text"]

	# Nothing matching is an answerable outcome, not an exception.
	assert workspace_lookup.lookup("cat", query="zzzznothing")["error"]
