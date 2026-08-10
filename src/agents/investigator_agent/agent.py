"""Investigator agent — route a free-text instruction to the right research path.

The agent accepts one text prompt and turns it into one of three best-effort
actions:

- paper: resolve/download a paper and run the existing paper pipeline
- paper_search: find several papers on the web, then download each one
- web: search the web and return the top results plus fetched excerpts
- url: scrape a direct URL, downloading PDFs when possible

The output is always plain text so callers can display it directly.  Routing
and execution follow a bounded Reflexion loop: an actor selects a strategy, an
evaluator checks the resulting artifact, and a reflection becomes memory for a
revised attempt (and for later runs of the same request).
"""
from __future__ import annotations

import html as html_module
import json
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Literal, TypedDict
from urllib.parse import parse_qs, unquote, urlparse

import requests
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field
from pdfminer.high_level import extract_text as extract_pdf_text

from src.agents.paper_agent import run as run_paper_agent
from src.common.utils import storage
from src.common.utils.logging import trace
from src.common.utils.logging.runlog import GraphRunLog
from src.llm.registry import chat, chat_with_structured_output, get_llm

_USER_AGENT = "novadraft-investigator-agent/1.0"
_REQUEST_TIMEOUT = 20
_MAX_RESULT_COUNT = 5
_MAX_PAPER_DOWNLOADS = 5
_MAX_PAGE_CHARS = 4000
_DEFAULT_MAX_ATTEMPTS = 2
_MAX_REFLECTIONS_PER_TASK = 5

_WEB_ANSWER_SYSTEM_PROMPT = (
	"Answer the user's query using only the supplied fetched web-page content. "
	"Do not use outside knowledge or assume facts not supported by those pages. "
	"If the pages do not contain enough information, say so plainly. "
	"Give a concise, direct, however detailed answer."
)


class InvestigatorDecision(BaseModel):
	action: Literal["paper", "paper_search", "web", "url"] = Field(
		description="Choose paper for one known paper, paper_search to find and download several papers, web for a general web search, or url for a direct page to scrape/download.",
	)
	paper_count: int = Field(
		default=1,
		ge=1,
		le=_MAX_PAPER_DOWNLOADS,
		description="Number of papers to find and download when action is paper_search.",
	)
	should_fetch_pages: bool = Field(
		default=False,
		description="For web searches, decide whether the agent should open and scrape result pages after the search.",
	)
	query: str = Field(
		default="",
		description="The best search query or paper title to pass downstream. Leave empty only when action is url and the URL itself is enough.",
	)
	url: str = Field(
		default="",
		description="A direct URL when the user supplied one and wants that URL scraped/downloaded.",
	)
	save_requested: bool = Field(
		default=False,
		description="True when the user explicitly asked to download, save, scrape, crawl, or fetch the content.",
	)
	reason: str = Field(
		default="",
		description="Short explanation of why this action was chosen.",
	)


class InvestigatorReflection(BaseModel):
	"""A compact, reusable lesson produced after an unsuccessful attempt."""
	lesson: str = Field(
		default="",
		description="One concrete instruction for the next attempt; do not restate the whole task.",
	)


_DECISION_SYSTEM_PROMPT = (
	"You route a single user instruction to the right research action. "
	"Pick exactly one action: paper, paper_search, web, or url. "
	"Use paper when the text is about a paper, article, preprint, DOI, arXiv ID, or asks to search/download a paper. "
	"Use paper_search when the user asks to find, search for, or download multiple papers (for example 'download 3 papers about transformers'). Set paper_count to the requested number, or 3 when they say 'some' or do not specify a number. "
	"Use web when the text asks to search the web generally, compare claims on the web, or learn about a topic without naming a direct URL. "
	"Use url only when the user provides a concrete URL and wants that page scraped, downloaded, or inspected. "
	"For every web query that asks for a factual answer, set should_fetch_pages to true. Set it to false only when the user explicitly asks for search results, links, or titles without an answer. "
	"Set save_requested true only when the user asks to download, save, scrape, crawl, or fetch content. "
	"Return the best downstream query or URL. Keep the query concise and literal.")

_REFLECTION_SYSTEM_PROMPT = (
	"You are the reflection component in a Reflexion research agent. "
	"Given a user's request, the attempted route, and evaluator feedback, write one concise, "
	"actionable lesson for the next attempt. Focus on changing the search target, route, or "
	"retrieval strategy. Do not claim the task was completed and do not invent facts.")


class InvestigatorState(TypedDict, total=False):
	text: str
	session_id: str
	mode: str
	query: str
	paper_count: int
	url: str
	save_requested: bool
	should_fetch_pages: bool
	research_result: dict
	attempt: int
	max_attempts: int
	reflections: list[str]
	evaluation: dict
	result: str


@dataclass
class SearchResult:
	title: str
	url: str
	snippet: str = ""


class _HTMLTextExtractor(HTMLParser):
	def __init__(self) -> None:
		super().__init__()
		self._parts: list[str] = []
		self._skip_depth = 0
		self._block_tags = {
			"article", "aside", "blockquote", "div", "footer", "header",
			"h1", "h2", "h3", "h4", "li", "main", "p", "pre", "section",
			"table", "tr",
		}

	def handle_starttag(self, tag: str, attrs):
		if tag in {"script", "style", "noscript"}:
			self._skip_depth += 1
		elif tag in self._block_tags and self._parts and not self._parts[-1].endswith("\n"):
			self._parts.append("\n")

	def handle_endtag(self, tag: str):
		if tag in {"script", "style", "noscript"} and self._skip_depth:
			self._skip_depth -= 1
		elif tag in self._block_tags and self._parts and not self._parts[-1].endswith("\n"):
			self._parts.append("\n")

	def handle_data(self, data: str):
		if self._skip_depth:
			return
		text = re.sub(r"\s+", " ", data).strip()
		if text:
			self._parts.append(text)

	def get_text(self) -> str:
		text = " ".join(self._parts)
		text = re.sub(r"\n\s+", "\n", text)
		text = re.sub(r"\s*\n\s*", "\n", text)
		return re.sub(r"[ \t]+", " ", text).strip()


def _session_workspace(session_id: str) -> str:
	"""Validate a session identifier before using it as a workspace directory."""
	session_id = _normalize(session_id)
	if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", session_id):
		raise ValueError(
			"session_id must contain only letters, numbers, underscores, or hyphens "
			"and must start with a letter or number."
		)
	return session_id


def _ensure_workspace(session_id: str) -> Path:
	category_dir = storage.category_dir(session_id)
	meta_path = category_dir / "meta.json"
	if not meta_path.exists():
		category_dir.mkdir(parents=True, exist_ok=True)
		now = storage.now_iso()
		meta = {
			"category_slug": session_id,
			"name": f"Investigator session {session_id}",
			"description": "Workspace used by the investigator agent for one ad hoc research session.",
			"source_url": None,
			"created_at": now,
			"updated_at": now,
		}
		meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
	return category_dir


def _normalize(text: str) -> str:
	return re.sub(r"\s+", " ", (text or "").strip())


def _first_url(text: str) -> str | None:
	match = re.search(r"https?://\S+", text or "")
	if match is None:
		return None
	return match.group(0).rstrip(").,;\"]}'")


def _extract_target(text: str, patterns: list[str]) -> str:
	clean = _normalize(text)
	for pattern in patterns:
		match = re.search(pattern, clean, flags=re.IGNORECASE)
		if match:
			candidate = _normalize(match.group(1)).strip(" \"'`")
			if candidate:
				return candidate
	return clean


def _requested_paper_count(text: str) -> int | None:
	"""Return a requested multi-paper count, using three for vague plurals."""
	match = re.search(
		r"\b(?:download|fetch|find|search(?:\s+for)?)\s+(?:(\d+)|one|two|three|four|five|some|several)\s+(?:papers?|articles?|preprints?)\b",
		text,
		flags=re.IGNORECASE,
	)
	if not match:
		return None
	if match.group(1):
		return max(1, min(int(match.group(1)), _MAX_PAPER_DOWNLOADS))
	words = match.group(0).lower()
	for word, count in (("one", 1), ("two", 2), ("three", 3), ("four", 4), ("five", 5)):
		if re.search(rf"\b{word}\b", words):
			return count
	return 3


def _paper_search_topic(text: str) -> str:
	"""Remove a multi-paper request prefix, preserving the research topic."""
	topic = re.sub(
		r"^(?:search(?:\s+for)?|find|download|fetch|get)\s+(?:(?:\d+|one|two|three|four|five|some|several)\s+)?(?:papers?|articles?|preprints?)\s*(?:(?:about|on|for)\s+)?",
		"",
		_normalize(text),
		flags=re.IGNORECASE,
	)
	return _normalize(topic).strip(":- ") or _normalize(text)


def _heuristic_decide_request(text: str) -> dict:
	clean = _normalize(text)
	lower = clean.lower()
	url = _first_url(clean)
	save_requested = any(term in lower for term in ("download", "save", "scrape", "crawl", "fetch"))
	paper_requested = any(term in lower for term in ("paper", "arxiv", "doi", "preprint", "manuscript"))
	paper_count = _requested_paper_count(clean)
	web_requested = any(term in lower for term in ("search the web", "web search", "search online", "search internet", "look up online"))
	should_fetch_pages = any(term in lower for term in ("summarize", "summary", "download", "scrape", "extract", "read", "inspect"))

	if url and (save_requested or "scrape" in lower or "download" in lower):
		return {"mode": "url", "url": url, "query": url, "save_requested": save_requested, "should_fetch_pages": False, "decision_reason": "heuristic fallback"}
	if paper_requested:
		if paper_count is not None:
			return {
				"mode": "paper_search",
				"query": _paper_search_topic(clean),
				"paper_count": paper_count,
				"url": url or None,
				"save_requested": True,
				"should_fetch_pages": False,
				"decision_reason": "heuristic fallback",
			}
		query = _extract_target(
			clean,
			[
				r"(?:search|find|download|fetch|summarize|summarise|read|get)\s+(?:the\s+)?(?:paper|article|preprint)\s*[:\-]\s*(.+)$",
				r"(?:paper|article|preprint)\s*(?:called|named|titled|about|on)\s+(.+)$",
				r"(?:paper|article|preprint)\s*[:\-]\s*(.+)$",
			],
		)
		return {"mode": "paper", "query": query, "paper_count": 1, "url": url or None, "save_requested": save_requested, "should_fetch_pages": False, "decision_reason": "heuristic fallback"}
	query = _extract_target(
		clean,
		[
			r"(?:search the web for|search web for|search online for|search for|look up|find)\s+(.+)$",
			r"(?:web search|internet search)\s*[:\-]\s*(.+)$",
		],
	)
	return {"mode": "web", "query": query, "paper_count": 1, "url": url or None, "save_requested": save_requested, "should_fetch_pages": should_fetch_pages, "decision_reason": "heuristic fallback"}


def _decide_request(text: str, reflections: list[str] | None = None) -> dict:
	clean = _normalize(text)
	memory = [_normalize(item) for item in (reflections or []) if _normalize(item)]
	actor_input = clean
	if memory:
		actor_input += "\n\nLessons from unsuccessful earlier attempts:\n" + "\n".join(
			f"- {lesson}" for lesson in memory[-_MAX_REFLECTIONS_PER_TASK:]
		)
		actor_input += "\nUse these lessons to choose a revised strategy."
	try:
		decision = chat_with_structured_output(
			get_llm("reasoner", temperature=0),
			_DECISION_SYSTEM_PROMPT,
			actor_input,
			InvestigatorDecision,
		)
	except Exception:
		return _heuristic_decide_request(clean)

	url = _normalize(decision.url) or _first_url(clean) or ""
	query = _normalize(decision.query)
	action = _normalize(getattr(decision, "action", ""))
	if action not in {"paper", "paper_search", "web", "url"}:
		return _heuristic_decide_request(clean)
	if action == "url":
		if not url:
			url = _first_url(clean) or ""
		if not query:
			query = url or clean
	else:
		if not query:
			if action == "paper_search":
				query = _paper_search_topic(clean)
			elif action == "paper":
				query = _extract_target(
					clean,
					[
						r"(?:search|find|download|fetch|summarize|summarise|read|get)\s+(?:the\s+)?(?:paper|article|preprint)\s*[:\-]\s*(.+)$",
						r"(?:paper|article|preprint)\s*(?:called|named|titled|about|on)\s+(.+)$",
						r"(?:paper|article|preprint)\s*[:\-]\s*(.+)$",
					],
				)
			else:
				query = _extract_target(
					clean,
					[
						r"(?:search the web for|search web for|search online for|search for|look up|find)\s+(.+)$",
						r"(?:web search|internet search)\s*[:\-]\s*(.+)$",
					],
				)

	if action == "url" and url:
		return {"mode": "url", "url": url, "query": query, "paper_count": 1, "save_requested": bool(decision.save_requested), "should_fetch_pages": False, "decision_reason": decision.reason}
	if action == "paper_search":
		return {
			"mode": "paper_search",
			"query": query,
			"paper_count": max(1, min(int(decision.paper_count), _MAX_PAPER_DOWNLOADS)),
			"url": url or None,
			"save_requested": True,
			"should_fetch_pages": False,
			"decision_reason": decision.reason,
		}
	if action == "paper":
		return {"mode": "paper", "query": query, "paper_count": 1, "url": url or None, "save_requested": bool(decision.save_requested), "should_fetch_pages": False, "decision_reason": decision.reason}
	return {
		"mode": "web",
		"query": query,
		"paper_count": 1,
		"url": url or None,
		"save_requested": bool(decision.save_requested),
		"should_fetch_pages": bool(decision.should_fetch_pages),
		"decision_reason": decision.reason,
	}


def _route_after_interpret(state: InvestigatorState) -> str:
	return state["mode"]


def _decode_duckduckgo_url(href: str) -> str:
	parsed = urlparse(href)
	if parsed.netloc.endswith("duckduckgo.com") and parsed.path == "/l/":
		query = parse_qs(parsed.query)
		uddg = query.get("uddg", [None])[0]
		if uddg:
			return unquote(uddg)
	return href


def _strip_html(text: str) -> str:
	parser = _HTMLTextExtractor()
	parser.feed(text)
	return parser.get_text()


def _html_title(html_text: str) -> str:
	match = re.search(r"<title[^>]*>(.*?)</title>", html_text, flags=re.IGNORECASE | re.DOTALL)
	if not match:
		return ""
	return re.sub(r"\s+", " ", html_module.unescape(_strip_html(match.group(1)))).strip()


def _extract_html_excerpt(html_text: str) -> str:
	paragraphs = []
	for chunk in re.findall(r"<p[^>]*>(.*?)</p>", html_text, flags=re.IGNORECASE | re.DOTALL):
		text = _strip_html(chunk)
		if len(text) >= 40:
			paragraphs.append(text)
	if paragraphs:
		return "\n\n".join(paragraphs[:5])
	return _strip_html(html_text)


def _search_web(query: str) -> list[SearchResult]:
	response = requests.get(
		"https://duckduckgo.com/html/",
		params={"q": query},
		headers={"User-Agent": _USER_AGENT},
		timeout=_REQUEST_TIMEOUT,
	)
	response.raise_for_status()
	html_text = response.text

	results: list[SearchResult] = []
	pattern = re.compile(
		r'<a[^>]*class="[^"]*result__a[^"]*"[^>]*href="(?P<href>[^"]+)"[^>]*>(?P<title>.*?)</a>',
		flags=re.IGNORECASE | re.DOTALL,
	)
	for match in pattern.finditer(html_text):
		href = _decode_duckduckgo_url(html_module.unescape(match.group("href")))
		title = re.sub(r"\s+", " ", _strip_html(html_module.unescape(match.group("title")))).strip()
		snippet = ""
		tail = html_text[match.end(): match.end() + 1000]
		snippet_match = re.search(
			r'<a[^>]*class="[^"]*result__snippet[^"]*"[^>]*>(?P<snippet>.*?)</a>|'
			r'<div[^>]*class="[^"]*result__snippet[^"]*"[^>]*>(?P<divsnippet>.*?)</div>',
			tail,
			flags=re.IGNORECASE | re.DOTALL,
		)
		if snippet_match:
			snippet = snippet_match.group("snippet") or snippet_match.group("divsnippet") or ""
			snippet = re.sub(r"\s+", " ", _strip_html(html_module.unescape(snippet))).strip()
		if title and href:
			results.append(SearchResult(title=title, url=href, snippet=snippet))
		if len(results) >= _MAX_RESULT_COUNT:
			break
	return results


def _download_path(session_id: str, name: str, suffix: str) -> Path:
	downloads_dir = storage.workspace_root() / session_id / "downloads"
	downloads_dir.mkdir(parents=True, exist_ok=True)
	safe = re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-").lower() or "download"
	return downloads_dir / f"{safe}{suffix}"


def _scrape_url(url: str, *, session_id: str, save_requested: bool = False) -> dict:
	response = requests.get(
		url,
		headers={"User-Agent": _USER_AGENT},
		timeout=_REQUEST_TIMEOUT,
		allow_redirects=True,
	)
	response.raise_for_status()
	content_type = (response.headers.get("content-type") or "").lower()
	final_url = response.url

	if "application/pdf" in content_type or final_url.lower().endswith(".pdf"):
		save_path = _download_path(session_id, Path(urlparse(final_url).path).stem or "paper", ".pdf")
		save_path.write_bytes(response.content)
		try:
			extracted = extract_pdf_text(str(save_path))
		except Exception as exc:  # noqa: BLE001 - best effort is enough here
			extracted = f"[PDF text extraction failed: {type(exc).__name__}: {exc}]"
		return {
			"kind": "pdf",
			"url": final_url,
			"saved_to": str(save_path),
			"title": Path(urlparse(final_url).path).name or save_path.stem,
			"text": extracted[:_MAX_PAGE_CHARS],
		}

	html_text = response.text
	title = _html_title(html_text) or final_url
	excerpt = _extract_html_excerpt(html_text)[:_MAX_PAGE_CHARS]
	saved_to = None
	if save_requested:
		save_path = _download_path(session_id, title, ".html")
		save_path.write_text(html_text, encoding="utf-8")
		saved_to = str(save_path)
	return {
		"kind": "html",
		"url": final_url,
		"saved_to": saved_to,
		"title": title,
		"text": excerpt,
	}


def _paper_to_text(result: dict, query: str) -> str:
	lines = [
		"Paper research",
		f"Query: {query}",
		f"Status: {result.get('status', 'unknown')}",
	]

	if result.get("error"):
		lines.append("")
		lines.append(f"Error: {result['error']}")
		return "\n".join(lines)

	if result.get("status") != "ok":
		if result.get("status") == "ambiguous":
			lines.append("")
			lines.append(result.get("message") or "Ambiguous paper query.")
			candidates = result.get("candidates") or []
			for idx, candidate in enumerate(candidates, start=1):
				lines.append("")
				lines.append(f"{idx}. {candidate.get('title') or 'Untitled'}")
				if candidate.get("authors"):
					lines.append(f"   Authors: {', '.join(candidate.get('authors') or [])}")
				if candidate.get("year"):
					lines.append(f"   Year: {candidate.get('year')}")
				if candidate.get("doi"):
					lines.append(f"   DOI: {candidate.get('doi')}")
				if candidate.get("arxiv_id"):
					lines.append(f"   arXiv: {candidate.get('arxiv_id')}")
				if candidate.get("pdf_url"):
					lines.append(f"   PDF: {candidate.get('pdf_url')}")
			return "\n".join(lines)
		if result.get("status") == "needs_source":
			lines.append("")
			lines.append(result.get("fetch", {}).get("message") or "The paper was found, but no downloadable full text is available.")
			return "\n".join(lines)
		lines.append("")
		lines.append(result.get("message") or "The paper workflow did not complete.")
		return "\n".join(lines)

	paper = result.get("fetch", {}).get("paper") or {}
	metadata = paper.get("metadata") or {}
	source = paper.get("source") or {}
	summary = result.get("summary") or {}
	extract = result.get("extract") or {}
	gaps = result.get("gaps") or {}

	lines.extend([
		"",
		f"Paper ID: {paper.get('paper_id') or result.get('paper_id') or 'unknown'}",
		f"Title: {metadata.get('title') or 'unknown'}",
		f"Authors: {', '.join(metadata.get('authors') or []) or 'unknown'}",
		f"Year: {metadata.get('year') or 'unknown'}",
		f"Source: {source.get('resolved_via') or 'unknown'}",
	])
	if source.get("source_url"):
		lines.append(f"URL: {source.get('source_url')}")
	if paper.get("warnings"):
		lines.append("Warnings:")
		for warning in paper.get("warnings") or []:
			lines.append(f"- {warning}")

	lines.extend(["", "Summary:"])
	if summary:
		summary_text = summary.get("summary") or summary.get("text") or summary.get("result")
		if isinstance(summary_text, str) and summary_text.strip():
			lines.append(summary_text.strip())
		else:
			lines.append(json.dumps(summary, indent=2, ensure_ascii=False, default=str))
	else:
		lines.append("No summary was returned.")

	if extract:
		lines.extend(["", "Extract:"])
		lines.append(json.dumps(extract, indent=2, ensure_ascii=False, default=str))

	if gaps:
		lines.extend(["", "Gaps:"])
		lines.append(json.dumps(gaps, indent=2, ensure_ascii=False, default=str))

	return "\n".join(lines)


def _web_to_text(result: dict, query: str) -> str:
	answer = result.get("answer")
	if isinstance(answer, str) and answer.strip():
		return answer.strip()

	lines = ["Web research", f"Query: {query}"]

	if result.get("error"):
		lines.extend(["", f"Error: {result['error']}"])
		return "\n".join(lines)

	results = result.get("search_results") or []
	pages = result.get("pages") or []

	lines.extend(["", "Search results:"])
	if not results:
		lines.append("No results found.")
	else:
		for idx, item in enumerate(results, start=1):
			lines.append(f"{idx}. {item.get('title')}")
			lines.append(f"   {item.get('url')}")
			if item.get("snippet"):
				lines.append(f"   {item.get('snippet')}")

	if pages:
		lines.extend(["", "Fetched pages:"])
		for idx, page in enumerate(pages, start=1):
			lines.append(f"{idx}. {page.get('title') or page.get('url')}")
			lines.append(f"   {page.get('url')}")
			if page.get("saved_to"):
				lines.append(f"   Saved to: {page.get('saved_to')}")
			if page.get("text"):
				lines.append("")
				lines.append(page.get("text"))
				lines.append("")

	return "\n".join(lines).strip()


def _url_to_text(result: dict, url: str) -> str:
	answer = result.get("answer")
	if isinstance(answer, str) and answer.strip():
		return answer.strip()

	lines = ["URL scrape", f"URL: {url}", f"Kind: {result.get('kind', 'unknown')}" ]
	if result.get("error"):
		lines.extend(["", f"Error: {result['error']}"])
		return "\n".join(lines)
	if result.get("saved_to"):
		lines.append(f"Saved to: {result.get('saved_to')}")
	if result.get("title"):
		lines.append(f"Title: {result.get('title')}")
	if result.get("text"):
		lines.extend(["", result.get("text")])
	return "\n".join(lines)


def _multi_paper_to_text(result: dict, query: str) -> str:
	"""Render the search candidates and each individual paper-pipeline result."""
	lines = ["Multi-paper research", f"Query: {query}"]
	if result.get("error"):
		return "\n".join(lines + ["", f"Error: {result['error']}"])

	candidates = result.get("search_results") or []
	lines.extend(["", f"Requested: {result.get('requested_count', 0)} papers", "", "Web candidates:"])
	for idx, candidate in enumerate(candidates, start=1):
		lines.append(f"{idx}. {candidate.get('title') or 'Untitled'}")
		lines.append(f"   {candidate.get('url') or 'No URL'}")

	for idx, item in enumerate(result.get("papers") or [], start=1):
		lines.extend(["", f"Paper {idx}: {item.get('title') or item.get('url') or 'Untitled'}"])
		lines.append(_paper_to_text(item.get("result") or {}, item.get("query") or query))
	return "\n".join(lines)


def _reflection_memory_path(session_id: str) -> Path:
	return storage.category_dir(session_id) / "investigator_reflections.json"


def _task_key(text: str) -> str:
	"""A stable, content-addressed key keeps lessons scoped to one request."""
	import hashlib
	return hashlib.sha256(_normalize(text).lower().encode("utf-8")).hexdigest()


def _load_reflections(session_id: str, text: str) -> list[str]:
	path = _reflection_memory_path(session_id)
	if not path.exists():
		return []
	try:
		records = json.loads(path.read_text(encoding="utf-8"))
		lessons = records.get(_task_key(text), []) if isinstance(records, dict) else []
		return [_normalize(item) for item in lessons if isinstance(item, str) and _normalize(item)][-_MAX_REFLECTIONS_PER_TASK:]
	except (OSError, json.JSONDecodeError):
		return []


def _store_reflection(session_id: str, text: str, lesson: str) -> None:
	lesson = _normalize(lesson)
	if not lesson:
		return
	path = _reflection_memory_path(session_id)
	try:
		records = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
		if not isinstance(records, dict):
			records = {}
	except (OSError, json.JSONDecodeError):
		records = {}
	key = _task_key(text)
	lessons = [item for item in records.get(key, []) if isinstance(item, str) and _normalize(item)]
	if lesson not in lessons:
		lessons.append(lesson)
	records[key] = lessons[-_MAX_REFLECTIONS_PER_TASK:]
	path.write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")


def actor(state: InvestigatorState) -> dict:
	"""Reflexion actor: route the request while conditioning on prior lessons."""
	_ensure_workspace(state["session_id"])
	attempt = int(state.get("attempt", 0)) + 1
	reflections = list(state.get("reflections") or [])
	if attempt == 1:
		reflections = _load_reflections(state["session_id"], state["text"])
	decision = _decide_request(state["text"], reflections)
	trace.log_event("investigator_actor", attempt=attempt, reflections=len(reflections), mode=decision["mode"])
	return {**decision, "attempt": attempt, "reflections": reflections}


def interpret(state: InvestigatorState) -> dict:
	"""Backward-compatible name for the investigator's actor stage."""
	return actor(state)


def _answer_from_fetched_pages(query: str, pages: list[dict]) -> str | None:
	"""Ask DeepSeek to answer *query* from successfully extracted page text."""
	page_sections = []
	for page in pages:
		content = _normalize(str(page.get("text") or ""))
		if not content:
			continue
		page_sections.append(
			f"Source: {page.get('url') or 'unknown'}\n"
			f"Title: {page.get('title') or 'untitled'}\n"
			f"Content:\n{content}"
		)

	if not page_sections:
		return None

	user_prompt = f"Query:\n{query}\n\nFetched pages:\n\n" + "\n\n---\n\n".join(page_sections)
	return chat(get_llm("cheap", temperature=0), _WEB_ANSWER_SYSTEM_PROMPT, user_prompt).strip()


def paper_node(state: InvestigatorState) -> dict:
	session_id = state["session_id"]
	_ensure_workspace(session_id)
	try:
		result = run_paper_agent(session_id, state["query"], stream=False)
	except Exception as exc:  # noqa: BLE001 - return a readable text error
		result = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
	return {"research_result": result}


def paper_search_node(state: InvestigatorState) -> dict:
	"""Find paper landing pages before the bounded sequential download step."""
	search_query = f"{state['query']} research paper"
	try:
		results = _search_web(search_query)
	except Exception as exc:  # noqa: BLE001 - return a readable text error
		return {"research_result": {"error": f"{type(exc).__name__}: {exc}"}}
	return {
		"research_result": {
			"requested_count": state["paper_count"],
			"search_results": [result.__dict__ for result in results],
			"papers": [],
		}
	}


def download_papers_node(state: InvestigatorState) -> dict:
	"""Run the existing one-paper workflow once for each web candidate."""
	result = state["research_result"]
	if result.get("error"):
		return {}

	_ensure_workspace(state["session_id"])
	papers = []
	for candidate in (result.get("search_results") or [])[:state["paper_count"]]:
		query = candidate.get("url") or candidate.get("title") or state["query"]
		try:
			paper_result = run_paper_agent(state["session_id"], query, stream=False)
		except Exception as exc:  # noqa: BLE001 - one failed candidate must not stop the session
			paper_result = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
		papers.append({
			"title": candidate.get("title"),
			"url": candidate.get("url"),
			"query": query,
			"result": paper_result,
		})
	return {"research_result": {**result, "papers": papers}}


def web_node(state: InvestigatorState) -> dict:
	query = state["query"]
	session_id = state["session_id"]
	try:
		results = _search_web(query)
	except Exception as exc:  # noqa: BLE001 - return a readable text error
		return {"research_result": {"error": f"{type(exc).__name__}: {exc}"}}
	result = {"search_results": [r.__dict__ for r in results], "pages": []}

	should_fetch_pages = bool(state.get("should_fetch_pages"))
	if should_fetch_pages and results:
		for item in results[:2]:
			try:
				result["pages"].append(_scrape_url(item.url, session_id=session_id, save_requested=state.get("save_requested", False)))
			except Exception as exc:  # noqa: BLE001 - keep partial progress
				result["pages"].append({"url": item.url, "title": item.title, "error": f"{type(exc).__name__}: {exc}"})
		try:
			answer = _answer_from_fetched_pages(query, result["pages"])
			if answer:
				result["answer"] = answer
		except Exception as exc:  # noqa: BLE001 - preserve fetched pages if the LLM is unavailable
			result["answer_error"] = f"{type(exc).__name__}: {exc}"
	return {"research_result": result}


def url_node(state: InvestigatorState) -> dict:
	try:
		result = _scrape_url(state["url"], session_id=state["session_id"], save_requested=state.get("save_requested", False))
	except Exception as exc:  # noqa: BLE001 - return a readable text error
		return {"research_result": {"error": f"{type(exc).__name__}: {exc}"}}

	try:
		answer = _answer_from_fetched_pages(state["query"], [result])
		if answer:
			result["answer"] = answer
	except Exception as exc:  # noqa: BLE001 - preserve the fetched page if the LLM is unavailable
		result["answer_error"] = f"{type(exc).__name__}: {exc}"
	return {"research_result": result}


def evaluate(state: InvestigatorState) -> dict:
	"""Reflexion evaluator with observable completion criteria.

	This deliberately evaluates delivery, not factual truth: the research nodes
	already preserve their source material, while a deterministic evaluator keeps
	retries safe and predictable when no LLM provider is configured.
	"""
	mode = state["mode"]
	result = state.get("research_result") or {}
	feedback = ""
	if result.get("error"):
		feedback = f"The {mode} route failed: {result['error']}"
	elif mode == "paper" and result.get("status") != "ok":
		feedback = result.get("message") or f"The paper workflow ended with status {result.get('status', 'unknown')}."
	elif mode == "paper_search" and not result.get("papers"):
		feedback = "No candidate paper completed; refine the paper title/topic or use a more specific source."
	elif mode == "web" and not result.get("answer") and not result.get("search_results"):
		feedback = "The web search returned no results; revise the search query."
	elif mode == "url" and not result.get("text"):
		feedback = "The supplied URL produced no readable content; try its canonical or downloadable form."

	success = not feedback
	evaluation = {"success": success, "feedback": feedback, "attempt": state.get("attempt", 1)}
	trace.log_event("investigator_evaluator", **evaluation)
	return {"evaluation": evaluation}


def _fallback_reflection(state: InvestigatorState) -> str:
	mode = state.get("mode", "research")
	feedback = (state.get("evaluation") or {}).get("feedback") or "The result was unusable."
	return f"After the {mode} route failed, use the evaluator feedback to make the next query or source more specific: {feedback}"


def reflect(state: InvestigatorState) -> dict:
	"""Convert evaluator feedback into a concise lesson and persist it as memory."""
	feedback = (state.get("evaluation") or {}).get("feedback") or "The result was unusable."
	try:
		reflection = chat_with_structured_output(
			get_llm("cheap", temperature=0),
			_REFLECTION_SYSTEM_PROMPT,
			f"User request:\n{state['text']}\n\nAttempted route: {state.get('mode')}\n\nEvaluator feedback:\n{feedback}",
			InvestigatorReflection,
		)
		lesson = _normalize(reflection.lesson)
	except Exception:
		lesson = ""
	if not lesson:
		lesson = _fallback_reflection(state)

	reflections = list(state.get("reflections") or [])
	if lesson not in reflections:
		reflections.append(lesson)
	reflections = reflections[-_MAX_REFLECTIONS_PER_TASK:]
	_store_reflection(state["session_id"], state["text"], lesson)
	trace.log_event("investigator_reflection", attempt=state.get("attempt", 1), lesson=lesson)
	return {"reflections": reflections}


def _route_after_evaluate(state: InvestigatorState) -> str:
	if (state.get("evaluation") or {}).get("success"):
		return "render"
	if int(state.get("attempt", 1)) >= int(state.get("max_attempts", _DEFAULT_MAX_ATTEMPTS)):
		return "render"
	return "reflect"


def render(state: InvestigatorState) -> dict:
	mode = state["mode"]
	if mode == "paper":
		output = _paper_to_text(state["research_result"], state["query"])
	elif mode == "paper_search":
		output = _multi_paper_to_text(state["research_result"], state["query"])
	elif mode == "web":
		output = _web_to_text(state["research_result"], state["query"])
	else:
		output = _url_to_text(state["research_result"], state["url"])
	return {"result": output}


def build_investigator_graph():
	g = StateGraph(InvestigatorState)
	g.add_node("actor", actor)
	g.add_node("paper", paper_node)
	g.add_node("paper_search", paper_search_node)
	g.add_node("download_papers", download_papers_node)
	g.add_node("web", web_node)
	g.add_node("url", url_node)
	g.add_node("evaluate", evaluate)
	g.add_node("reflect", reflect)
	g.add_node("render", render)

	g.add_edge(START, "actor")
	g.add_conditional_edges("actor", _route_after_interpret,
							{"paper": "paper", "paper_search": "paper_search", "web": "web", "url": "url"})
	g.add_edge("paper", "evaluate")
	g.add_edge("paper_search", "download_papers")
	g.add_edge("download_papers", "evaluate")
	g.add_edge("web", "evaluate")
	g.add_edge("url", "evaluate")
	g.add_conditional_edges("evaluate", _route_after_evaluate, {"render": "render", "reflect": "reflect"})
	g.add_edge("reflect", "actor")
	g.add_edge("render", END)
	return g.compile()


_GRAPH = None


def _get_graph():
	global _GRAPH
	if _GRAPH is None:
		_GRAPH = build_investigator_graph()
	return _GRAPH


def run(
	text: str,
	*,
	session_id: str,
	stream: bool = True,
	echo: bool | None = None,
	max_attempts: int = _DEFAULT_MAX_ATTEMPTS,
) -> str:
	"""Route a free-text instruction to paper fetch/summarize or web scraping.

	Returns plain text so callers can display it directly. The paper path uses
	the existing paper agent; the generic path searches the web and fetches the
	top hits when the prompt asks for more than just a search result list. Every
	download is stored in ``workspace/<session_id>/``.
	"""
	text = _normalize(text)
	if not text:
		raise ValueError("Text must not be empty.")
	session_id = _session_workspace(session_id)

	graph = _get_graph()
	if max_attempts < 1:
		raise ValueError("max_attempts must be at least 1.")
	# Keep the historical graph input shape for the common/default call; the
	# evaluator supplies its own default when this optional control is absent.
	inputs = {"text": text, "session_id": session_id}
	if max_attempts != _DEFAULT_MAX_ATTEMPTS:
		inputs["max_attempts"] = max_attempts
	if not stream:
		return graph.invoke(inputs)["result"]

	if echo is None:
		echo = True
	runlog = GraphRunLog("investigator_agent", workspace=session_id, run_hint=text)
	runlog.run(graph, inputs, echo=echo)
	if runlog.error:
		raise RuntimeError(runlog.error)
	return runlog.output


if __name__ == "__main__":
	import sys

	prompt = " ".join(sys.argv[2:]) or "what was the name of the 11th us president?"
	session_id = sys.argv[1] if len(sys.argv) > 1 else "investigator-session"
	print(run(prompt, session_id=session_id, stream=False))
