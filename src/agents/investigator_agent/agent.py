"""Investigator agent — route a free-text instruction to the right research path.

The agent accepts one text prompt and turns it into one of three best-effort
actions:

- paper: resolve/download a paper and run the existing paper pipeline
- web: search the web and return the top results plus fetched excerpts
- url: scrape a direct URL, downloading PDFs when possible

The output is always plain text so callers can display it directly.
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
from src.common.utils.logging.runlog import GraphRunLog
from src.llm.registry import chat_with_structured_output, get_llm

INVESTIGATOR_WORKSPACE = "investigator"
_USER_AGENT = "novadraft-investigator-agent/1.0"
_REQUEST_TIMEOUT = 20
_MAX_RESULT_COUNT = 5
_MAX_PAGE_CHARS = 4000


class InvestigatorDecision(BaseModel):
	action: Literal["paper", "web", "url"] = Field(
		description="Choose paper for paper/article/preprint/DOI/arXiv work, web for a general web search, or url for a direct page to scrape/download.",
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


_DECISION_SYSTEM_PROMPT = (
	"You route a single user instruction to the right research action. "
	"Pick exactly one action: paper, web, or url. "
	"Use paper when the text is about a paper, article, preprint, DOI, arXiv ID, or asks to search/download a paper. "
	"Use web when the text asks to search the web generally, compare claims on the web, or learn about a topic without naming a direct URL. "
	"Use url only when the user provides a concrete URL and wants that page scraped, downloaded, or inspected. "
	"Set save_requested true only when the user asks to download, save, scrape, crawl, or fetch content. "
	"Return the best downstream query or URL. Keep the query concise and literal.")


class InvestigatorState(TypedDict, total=False):
	text: str
	mode: str
	query: str
	url: str
	save_requested: bool
	research_result: dict
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


def _ensure_workspace() -> Path:
	category_dir = storage.category_dir(INVESTIGATOR_WORKSPACE)
	meta_path = category_dir / "meta.json"
	if not meta_path.exists():
		category_dir.mkdir(parents=True, exist_ok=True)
		now = storage.now_iso()
		meta = {
			"category_slug": INVESTIGATOR_WORKSPACE,
			"name": "Investigator",
			"description": "Workspace used by the investigator agent for ad hoc web and paper research.",
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


def _heuristic_decide_request(text: str) -> dict:
	clean = _normalize(text)
	lower = clean.lower()
	url = _first_url(clean)
	save_requested = any(term in lower for term in ("download", "save", "scrape", "crawl", "fetch"))
	paper_requested = any(term in lower for term in ("paper", "arxiv", "doi", "preprint", "manuscript"))
	web_requested = any(term in lower for term in ("search the web", "web search", "search online", "search internet", "look up online"))

	if url and (save_requested or "scrape" in lower or "download" in lower):
		return {"mode": "url", "url": url, "query": url, "save_requested": save_requested, "decision_reason": "heuristic fallback"}
	if paper_requested:
		query = _extract_target(
			clean,
			[
				r"(?:search|find|download|fetch|summarize|summarise|read|get)\s+(?:the\s+)?(?:paper|article|preprint)\s*[:\-]\s*(.+)$",
				r"(?:paper|article|preprint)\s*(?:called|named|titled|about|on)\s+(.+)$",
				r"(?:paper|article|preprint)\s*[:\-]\s*(.+)$",
			],
		)
		return {"mode": "paper", "query": query, "url": url or None, "save_requested": save_requested, "decision_reason": "heuristic fallback"}
	query = _extract_target(
		clean,
		[
			r"(?:search the web for|search web for|search online for|search for|look up|find)\s+(.+)$",
			r"(?:web search|internet search)\s*[:\-]\s*(.+)$",
		],
	)
	return {"mode": "web", "query": query, "url": url or None, "save_requested": save_requested, "decision_reason": "heuristic fallback"}


def _decide_request(text: str) -> dict:
	clean = _normalize(text)
	try:
		decision = chat_with_structured_output(
			get_llm("reasoner", temperature=0),
			_DECISION_SYSTEM_PROMPT,
			clean,
			InvestigatorDecision,
		)
	except Exception:
		return _heuristic_decide_request(clean)

	url = _normalize(decision.url) or _first_url(clean) or ""
	query = _normalize(decision.query)
	action = _normalize(getattr(decision, "action", ""))
	if action not in {"paper", "web", "url"}:
		return _heuristic_decide_request(clean)
	if action == "url":
		if not url:
			url = _first_url(clean) or ""
		if not query:
			query = url or clean
	else:
		if not query:
			if action == "paper":
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
		return {"mode": "url", "url": url, "query": query, "save_requested": bool(decision.save_requested), "decision_reason": decision.reason}
	if action == "paper":
		return {"mode": "paper", "query": query, "url": url or None, "save_requested": bool(decision.save_requested), "decision_reason": decision.reason}
	return {"mode": "web", "query": query, "url": url or None, "save_requested": bool(decision.save_requested), "decision_reason": decision.reason}


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


def _download_path(name: str, suffix: str) -> Path:
	downloads_dir = storage.workspace_root() / INVESTIGATOR_WORKSPACE / "downloads"
	downloads_dir.mkdir(parents=True, exist_ok=True)
	safe = re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-").lower() or "download"
	return downloads_dir / f"{safe}{suffix}"


def _scrape_url(url: str, *, save_requested: bool = False) -> dict:
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
		save_path = _download_path(Path(urlparse(final_url).path).stem or "paper", ".pdf")
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
		save_path = _download_path(title, ".html")
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


def interpret(state: InvestigatorState) -> dict:
	return _decide_request(state["text"])


def paper_node(state: InvestigatorState) -> dict:
	_ensure_workspace()
	try:
		result = run_paper_agent(INVESTIGATOR_WORKSPACE, state["query"], stream=False)
	except Exception as exc:  # noqa: BLE001 - return a readable text error
		result = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
	return {"research_result": result}


def web_node(state: InvestigatorState) -> dict:
	query = state["query"]
	try:
		results = _search_web(query)
	except Exception as exc:  # noqa: BLE001 - return a readable text error
		return {"research_result": {"error": f"{type(exc).__name__}: {exc}"}}
	result = {"search_results": [r.__dict__ for r in results], "pages": []}

	lower = state["text"].lower()
	should_fetch_pages = any(term in lower for term in ("summarize", "summary", "download", "scrape", "extract", "read"))
	if should_fetch_pages and results:
		for item in results[:2]:
			try:
				result["pages"].append(_scrape_url(item.url, save_requested=state.get("save_requested", False)))
			except Exception as exc:  # noqa: BLE001 - keep partial progress
				result["pages"].append({"url": item.url, "title": item.title, "error": f"{type(exc).__name__}: {exc}"})
	return {"research_result": result}


def url_node(state: InvestigatorState) -> dict:
	try:
		result = _scrape_url(state["url"], save_requested=state.get("save_requested", False))
	except Exception as exc:  # noqa: BLE001 - return a readable text error
		result = {"error": f"{type(exc).__name__}: {exc}"}
	return {"research_result": result}


def render(state: InvestigatorState) -> dict:
	mode = state["mode"]
	if mode == "paper":
		output = _paper_to_text(state["research_result"], state["query"])
	elif mode == "web":
		output = _web_to_text(state["research_result"], state["query"])
	else:
		output = _url_to_text(state["research_result"], state["url"])
	return {"result": output}


def build_investigator_graph():
	g = StateGraph(InvestigatorState)
	g.add_node("interpret", interpret)
	g.add_node("paper", paper_node)
	g.add_node("web", web_node)
	g.add_node("url", url_node)
	g.add_node("render", render)

	g.add_edge(START, "interpret")
	g.add_conditional_edges("interpret", _route_after_interpret,
							{"paper": "paper", "web": "web", "url": "url"})
	g.add_edge("paper", "render")
	g.add_edge("web", "render")
	g.add_edge("url", "render")
	g.add_edge("render", END)
	return g.compile()


_GRAPH = None


def _get_graph():
	global _GRAPH
	if _GRAPH is None:
		_GRAPH = build_investigator_graph()
	return _GRAPH


def run(text: str, *, stream: bool = True, echo: bool | None = None) -> str:
	"""Route a free-text instruction to paper fetch/summarize or web scraping.

	Returns plain text so callers can display it directly. The paper path uses
	the existing paper agent; the generic path searches the web and fetches the
	top hits when the prompt asks for more than just a search result list.
	"""
	text = _normalize(text)
	if not text:
		raise ValueError("Text must not be empty.")

	graph = _get_graph()
	inputs = {"text": text}
	if not stream:
		return graph.invoke(inputs)["result"]

	if echo is None:
		echo = True
	runlog = GraphRunLog("investigator_agent", workspace=INVESTIGATOR_WORKSPACE, run_hint=text)
	runlog.run(graph, inputs, echo=echo)
	if runlog.error:
		raise RuntimeError(runlog.error)
	return runlog.output


if __name__ == "__main__":
	import sys

	prompt = " ".join(sys.argv[1:]) or "search and download the paper: attention is all you need"
	print(run(prompt, stream=False))
