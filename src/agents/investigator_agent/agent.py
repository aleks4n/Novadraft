"""Investigator agent — route a free-text instruction to the right research path.

The agent accepts one text prompt and turns it into one of four best-effort
actions:

- paper: resolve/download a paper, extract it, and answer from its content
- paper_search: find several papers, download/extract them, and answer from them
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
import os
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Literal, TypedDict
from xml.etree import ElementTree as ET
from urllib.parse import parse_qs, unquote, urlparse

import requests
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field
from pdfminer.high_level import extract_text as extract_pdf_text

from src.common.tools.paper_fetch import fetch as fetch_paper
from src.common.tools.paper_fetch import PaperAlreadyExistsError
from src.common.tools.paper_extract import extract as extract_paper
from src.common.tools.paper_access import get_full_text
from src.common.utils import storage
from src.agents.investigator_agent import workspace_lookup
from src.common.utils.logging import trace
from src.common.utils.logging.runlog import GraphRunLog
from src.common.schemas.evidence import CitedAnswer, CitationClaim, Evidence
from src.llm.registry import chat, chat_with_structured_output, get_llm

_USER_AGENT = "novadraft-investigator-agent/1.0"
_REQUEST_TIMEOUT = 20
_MAX_RESULT_COUNT = 5
_MAX_PAPER_SEARCH_CANDIDATES = 15
_MAX_PAPER_DOWNLOADS = 5
_MAX_PAGE_CHARS = 4000
_DEFAULT_MAX_ATTEMPTS = 2
_MAX_REFLECTIONS_PER_TASK = 5
_MAX_PARALLEL_PAPER_DOWNLOADS = 3
# When explicitly enabled, retrieval failures render immediately. The default
# keeps them inside Reflexion so the evaluator can reject the attempt and the
# actor can retry with its persisted lesson.
ENABLE_DETERMINISTIC_FETCH_FALLBACK = False
_MAX_PAPER_CHARS = 50_000
_MAX_MULTI_PAPER_CHARS = 100_000

_WEB_ANSWER_SYSTEM_PROMPT = (
	"Answer the user's query using only the supplied fetched web-page content. "
	"Do not use outside knowledge or assume facts not supported by those pages. "
	"If the pages do not contain enough information, say so plainly. "
	"Give a concise, direct, however detailed answer."
)

_CITED_ANSWER_SYSTEM_PROMPT = (
	"Answer the user's question using only the supplied evidence. Return one concise "
	"answer and claims that support it. Every claim must cite one or more exact source_id "
	"values from the evidence. Do not invent sources or facts. Mark claims verified only "
	"when the evidence directly supports them."
)

_PAPER_ANSWER_SYSTEM_PROMPT = (
	"Answer the user's question using only the supplied paper content. Do not use "
	"outside knowledge or infer claims the papers do not support. If the content is "
	"insufficient, say so clearly. Give a direct answer and distinguish findings from "
	"limitations."
)


class InvestigatorDecision(BaseModel):
	action: Literal["workspace", "paper", "paper_search", "web", "url"] = Field(
		description="Choose workspace to read what this project already holds (its gaps, its saved papers, its ideas), paper for one known paper to fetch from outside, paper_search to find and download several papers, web for a general web search, or url for a direct page to scrape/download.",
	)
	workspace_ids: list[str] = Field(
		default_factory=list,
		description="For action=workspace: exact record ids to read, when the request names any (for example gap_04e60ada or arxiv_1706.03762). Leave empty to search the workspace by topic instead.",
	)
	section: str = Field(
		default="",
		description="For action=workspace: the name of one paper section to read, when the request asks about a specific part (for example 'experiments' or 'related work'). Leave empty for the summary.",
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


class InvestigatorEvaluation(BaseModel):
	"""A compact evaluation of whether the latest attempt produced a usable result."""
	success: bool = Field(
		default=False,
		description="True when the latest attempt produced a usable result for the user's request.",
	)
	feedback: str = Field(
		default="",
		description="A concise explanation of why the attempt succeeded or failed; useful for the next reflection step.",
	)
	reason: str = Field(
		default="",
		description="Short justification for the success flag.",
	)


_DECISION_SYSTEM_PROMPT = (
	"You route a single user instruction to the right research action. "
	"Pick exactly one action: workspace, paper, paper_search, web, or url. "
	"CHECK THE WORKSPACE FIRST. This project keeps its own knowledge base — the research gaps it has "
	"extracted, the papers it has already fetched and summarised, and the ideas it has developed. "
	"Use workspace whenever the request could be answered from that: when it names a record id "
	"(gap_..., arxiv_..., doi_..., hash_..., idea_...), when it asks what this project knows, holds, "
	"has already found or has already tried, or when it asks about a topic that the project plausibly "
	"has material on. Put any ids the request names into workspace_ids; otherwise leave workspace_ids "
	"empty and put the topic in query. Set section when the request is about one part of a paper. "
	"Going to the web for something already on disk is slower and gives a worse answer, so prefer "
	"workspace unless the request clearly needs the outside world — new literature, prior-art checks, "
	"anything published since, or a named paper the project does not have. "
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

_EVALUATION_SYSTEM_PROMPT = (
	"You are the evaluator in a Reflexion research agent. "
	"Judge whether the latest attempt produced a usable result for the user request. "
	"Evaluate delivery and usefulness of the artifact, not factual truth. "
	"Return success=true when the attempt produced readable, relevant content such as a paper result, paper candidates, a web answer, or a scraped URL. "
	"Return success=false when the attempt errored, produced no usable artifact, or left a major gap. "
	"Return concise feedback that would help the next reflection step."
)


class InvestigatorState(TypedDict, total=False):
	text: str
	session_id: str
	requested_mode: str
	sources: list[str]
	# ``workspace_key`` is the category-relative location used by downstream
	# agents, e.g. ``machine-learning/sessions/session-42``.
	workspace_key: str
	# The bare category slug, kept beside ``workspace_key`` rather than parsed out of it:
	# the workspace routes read ``<category>/gaps/...``, which is a sibling of the session
	# folder the fetch routes write into, not something under it.
	category_slug: str
	mode: str
	query: str
	workspace_ids: list[str]
	section: str
	paper_count: int
	url: str
	save_requested: bool
	should_fetch_pages: bool
	research_result: dict
	evidence: list[dict]
	claims: list[dict]
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


def _search_result_keys(result: SearchResult) -> set[str]:
	"""Return identity aliases across paper mirrors and URL variants."""
	value = f"{result.title} {result.url}"
	keys: set[str] = set()
	arxiv = re.search(r"arxiv\.org/(?:abs|pdf)/([0-9]{4}\.[0-9]{4,5})(?:v\d+)?", value, re.IGNORECASE)
	if arxiv:
		keys.add(f"arxiv:{arxiv.group(1)}")
	doi = re.search(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", value, re.IGNORECASE)
	if doi:
		keys.add(f"doi:{doi.group(0).rstrip(').,;').lower()}")
	title = re.sub(r"^\s*\[[^]]+\]\s*", "", result.title)
	title = re.sub(r"^\s*arxiv:\s*\d{4}\.\d{4,5}(?:v\d+)?\s*", "", title, flags=re.IGNORECASE)
	title = re.sub(
		r"\s*(?:[-|:]\s*)?(?:arxiv(?:\.org)?|google research|wikipedia)\s*$",
		"", title, flags=re.IGNORECASE,
	)
	title = re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()
	if title:
		keys.add(f"title:{title}")
	if not keys:
		keys.add(f"url:{result.url.rstrip('/').lower()}")
	return keys


def _is_arxiv_url(url: str) -> bool:
	return _canonical_arxiv_url(url) is not None


def _canonical_arxiv_url(url: str) -> str | None:
	"""Return a canonical arXiv abstract URL when an arXiv identifier is present."""
	parsed = urlparse(url)
	host = (parsed.hostname or "").lower()
	if "arxiv.org" not in host:
		return None

	# Accept urls like /abs/<id>, /pdf/<id>.pdf, /html/<id> (ar5iv mirror), etc.
	identifier_match = re.search(
		r"(?:/abs/|/pdf/|/html/)([0-9]{4}\.[0-9]{4,5})(?:v\d+)?(?:\.pdf)?",
		parsed.path,
		flags=re.IGNORECASE,
	)
	if not identifier_match:
		# Fallback: if path/query still contains a valid modern arXiv id.
		identifier_match = re.search(r"([0-9]{4}\.[0-9]{4,5})(?:v\d+)?", url)
	if not identifier_match:
		return None
	identifier = identifier_match.group(1)
	return f"https://arxiv.org/abs/{identifier}"


def _deduplicate_search_results(results: list[SearchResult]) -> list[SearchResult]:
	seen: set[str] = set()
	unique: list[SearchResult] = []
	for result in results:
		keys = _search_result_keys(result)
		if keys & seen:
			continue
		seen.update(keys)
		unique.append(result)
	return unique


def _evidence_id(source_type: str, index: int) -> str:
	return f"{source_type}-{index}"


def _web_evidence(results: list[dict], pages: list[dict]) -> list[dict]:
	evidence: list[dict] = []
	for index, item in enumerate(results, start=1):
		evidence.append(Evidence(
			source_id=_evidence_id("web", index), title=item.get("title") or "Untitled source",
			url=item.get("url"), source_type="web", snippet=item.get("snippet") or "",
			text=item.get("snippet") or "", credibility=0.25,
		).model_dump())
	for index, item in enumerate(pages, start=1):
		evidence.append(Evidence(
			source_id=_evidence_id("page", index), title=item.get("title") or "Untitled page",
			url=item.get("url"), source_type="pdf" if item.get("kind") == "pdf" else "web",
			text=item.get("text") or "", fetched=not bool(item.get("error")),
			credibility=0.75 if not item.get("error") else 0.0, error=item.get("error"),
		).model_dump())
	return evidence


def _claims_from_answer(answer: str | None, evidence: list[dict]) -> list[dict]:
	if not answer or not evidence:
		return []
	try:
		cited = chat_with_structured_output(
			get_llm("cheap", temperature=0), _CITED_ANSWER_SYSTEM_PROMPT,
			json.dumps({"answer": answer, "evidence": evidence}, ensure_ascii=False), CitedAnswer,
		)
		valid_ids = {item["source_id"] for item in evidence}
		return [claim.model_dump() for claim in cited.claims
				if set(claim.source_ids).issubset(valid_ids)]
	except Exception:  # best-effort citation tracking must not lose the answer
		return []

def _clip_paper_text(text: str, budget: int) -> str:
	text = (text or "").strip()
	if len(text) <= budget:
		return text
	head = int(budget * 0.7)
	return f"{text[:head]}\n\n[... paper truncated ...]\n\n{text[-(budget - head):]}"


def _cached_paper(cache_keys: list[str], query: str) -> tuple[str, dict, str] | None:
	"""Find an already extracted paper by identifier, source URL, or exact title."""
	query_norm = _normalize(query).lower()
	arxiv_match = re.search(
		r"(?:arxiv:\s*|arxiv\.org/(?:abs|pdf)/)?([a-z\-]+(?:\.[A-Z]{2})?/\d{7}|\d{4}\.\d{4,5})(?:v\d+)?",
		query, re.IGNORECASE,
	)
	doi_match = re.search(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", query, re.IGNORECASE)
	for cache_key in [key for key in cache_keys if key]:
		for paper_id in storage.list_paper_ids(cache_key):
			paper = storage.read_paper_json(cache_key, paper_id) or {}
			metadata = paper.get("metadata") or {}
			identifiers = metadata.get("identifiers") or {}
			source = paper.get("source") or {}
			matched = bool(
				(arxiv_match and _normalize(identifiers.get("arxiv_id") or "").lower()
				 == arxiv_match.group(1).lower())
				or (doi_match and _normalize(identifiers.get("doi") or "").lower()
					== doi_match.group(0).rstrip(".,);").lower())
				or (_normalize(source.get("source_url") or "").lower() == query_norm)
				or (_normalize(metadata.get("title") or "").lower() == query_norm)
			)
			if not matched:
				continue
			full_text = get_full_text(cache_key, paper_id) or ""
			if full_text.strip():
				trace.log_event("investigator cache hit", paper_id=paper_id,
								workspace=cache_key)
				return paper_id, paper, full_text
	return None


def _fetch_extract_paper(workspace_key: str, query: str,
							 *, cache_keys: list[str] | None = None) -> dict:
	"""Download and extract one paper without invoking the paper/gap pipeline."""
	inferred_keys = [workspace_key]
	if "/sessions/" in workspace_key:
		inferred_keys.append(workspace_key.split("/sessions/", 1)[0])
	if cached := _cached_paper([*inferred_keys, *(cache_keys or [])], query):
		paper_id, paper, full_text = cached
		return {
			"status": "ok", "paper_id": paper_id,
			"fetch": {"status": "ok", "paper": paper, "cached": True},
			"extract": {"status": "ok", "cached": True}, "full_text": full_text,
		}
	try:
		fetched = fetch_paper(workspace_key, query)
	except PaperAlreadyExistsError as exc:
		paper_id = exc.paper_id
		paper = storage.read_paper_json(workspace_key, paper_id) or {}
		fetched = {"status": "ok", "paper": paper}
	except Exception as exc:  # noqa: BLE001 - one source failure is a readable result
		return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}

	if fetched.get("status") != "ok":
		return dict(fetched)
	paper = fetched.get("paper") or {}
	paper_id = paper.get("paper_id")
	if not paper_id:
		return {"status": "error", "error": "The fetched paper has no paper_id."}
	try:
		extract_paper(workspace_key, paper_id)
	except Exception as exc:  # noqa: BLE001
		return {
			"status": "error", "paper_id": paper_id, "fetch": fetched,
			"error": f"Paper extraction failed: {type(exc).__name__}: {exc}",
		}
	# Extraction persists content back into paper.json; reload before reading it.
	paper = storage.read_paper_json(workspace_key, paper_id) or paper
	full_text = get_full_text(workspace_key, paper_id) or ""
	if not full_text.strip():
		return {
			"status": "error", "paper_id": paper_id, "fetch": {**fetched, "paper": paper},
			"error": "The paper was downloaded but no readable full text was extracted.",
		}
	return {
		"status": "ok", "paper_id": paper_id, "fetch": {**fetched, "paper": paper},
		"extract": {"status": "ok"}, "full_text": full_text,
	}


def _answer_from_papers(question: str, papers: list[dict]) -> tuple[str | None, list[dict], list[dict]]:
	"""Answer one question from extracted papers and attach source-bound claims."""
	evidence: list[dict] = []
	sections: list[str] = []
	remaining = _MAX_MULTI_PAPER_CHARS if len(papers) > 1 else _MAX_PAPER_CHARS
	for index, result in enumerate(papers, start=1):
		if result.get("status") != "ok":
			continue
		paper = result.get("fetch", {}).get("paper") or {}
		metadata = paper.get("metadata") or {}
		source = paper.get("source") or {}
		full_text = result.get("full_text") or ""
		budget = min(_MAX_PAPER_CHARS, remaining)
		if budget <= 0:
			break
		content = _clip_paper_text(full_text, budget)
		remaining -= len(content)
		source_id = _evidence_id("paper", index)
		title = metadata.get("title") or paper.get("paper_id") or "Paper source"
		sections.append(f"Source ID: {source_id}\nTitle: {title}\nContent:\n{content}")
		evidence.append(Evidence(
			source_id=source_id, title=title, url=source.get("source_url"),
			source_type="paper", text=_clip_paper_text(full_text, _MAX_PAGE_CHARS),
			fetched=True, credibility=0.9,
		).model_dump())
	if not sections:
		return None, evidence, []
	prompt = f"Question:\n{question}\n\nPapers:\n\n" + "\n\n---\n\n".join(sections)
	answer = chat(get_llm("cheap", temperature=0), _PAPER_ANSWER_SYSTEM_PROMPT, prompt).strip()
	claims = _claims_from_answer(answer, evidence)
	return answer or None, evidence, claims

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


def _workspace_component(value: str, *, field: str) -> str:
	"""Validate one path component supplied by an agent caller."""
	value = _normalize(value)
	if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", value):
		raise ValueError(
			f"{field} must contain only letters, numbers, underscores, or hyphens "
			"and must start with a letter or number."
		)
	return value


def _session_workspace(session_id: str, category_slug: str | None = None) -> str:
	"""Return the category-relative workspace path for one investigator session.

	Validator callers provide ``category_slug`` and therefore write beneath
	``workspace/<category>/sessions/<session_id>/``.  Omitting it retains the
	legacy standalone location for existing integrations.
	"""
	session_id = _workspace_component(session_id, field="session_id")
	if category_slug is None:
		return session_id
	category_slug = _workspace_component(category_slug, field="category_slug")
	return f"{category_slug}/sessions/{session_id}"


def _ensure_workspace(workspace_key: str) -> Path:
	category_dir = storage.category_dir(workspace_key)
	meta_path = category_dir / "meta.json"
	if not meta_path.exists():
		category_dir.mkdir(parents=True, exist_ok=True)
		now = storage.now_iso()
		meta = {
			"category_slug": workspace_key,
			"name": f"Investigator session {workspace_key.rsplit('/', 1)[-1]}",
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


# A workspace record id, written literally in the request. Distinctive enough to route on
# without an LLM: nothing else in a research question looks like `gap_04e60ada`.
_RECORD_ID_RE = re.compile(r"\b((?:gap|idea|arxiv|doi|hash)_[A-Za-z0-9._\-]+)\b")


def _heuristic_decide_request(text: str) -> dict:
	clean = _normalize(text)
	lower = clean.lower()
	url = _first_url(clean)

	# Before anything else: an id names a record this project already holds, and no web
	# search will find it. This is the one route the fallback can be certain about.
	if ids := _RECORD_ID_RE.findall(clean):
		return {
			"mode": "workspace",
			"query": clean,
			"workspace_ids": ids,
			"section": "",
			"paper_count": 1,
			"url": url or None,
			"save_requested": False,
			"should_fetch_pages": False,
			"decision_reason": "heuristic fallback: request names workspace record id(s)",
		}

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
	if action not in {"workspace", "paper", "paper_search", "web", "url"}:
		return _heuristic_decide_request(clean)
	if action == "workspace":
		ids = [_normalize(i) for i in (getattr(decision, "workspace_ids", None) or [])]
		return {
			"mode": "workspace",
			# The whole request is the fallback query. A workspace search scores over terms,
			# so the raw question works where an over-extracted phrase ("the gap") does not.
			"query": query or clean,
			"workspace_ids": [i for i in ids if i],
			"section": _normalize(getattr(decision, "section", "")),
			"paper_count": 1,
			"url": url or None,
			"save_requested": False,
			"should_fetch_pages": False,
			"decision_reason": decision.reason,
		}
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


def _search_web(query: str, max_results: int = _MAX_RESULT_COUNT) -> list[SearchResult]:
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
		if len(results) >= max_results:
			break
	return results


def _search_arxiv(query: str, max_results: int = _MAX_RESULT_COUNT) -> list[SearchResult]:
	"""Search arXiv directly as a reliable fallback for paper discovery."""
	response = requests.get(
		"https://export.arxiv.org/api/query",
		params={
			"search_query": f"all:{query}",
			"start": 0,
			"max_results": max_results,
			"sortBy": "relevance",
			"sortOrder": "descending",
		},
		headers={"User-Agent": _USER_AGENT},
		timeout=_REQUEST_TIMEOUT,
	)
	response.raise_for_status()

	root = ET.fromstring(response.text)
	ns = {"atom": "http://www.w3.org/2005/Atom"}
	results: list[SearchResult] = []
	for entry in root.findall("atom:entry", ns):
		title = re.sub(r"\s+", " ", (entry.findtext("atom:title", "", ns) or "")).strip()
		snippet = re.sub(r"\s+", " ", (entry.findtext("atom:summary", "", ns) or "")).strip()
		url = ""
		for link in entry.findall("atom:link", ns):
			href = (link.attrib.get("href") or "").strip()
			rel = (link.attrib.get("rel") or "").strip().lower()
			if href and ("/abs/" in href or rel == "alternate"):
				url = href
				break
		if not url:
			url = (entry.findtext("atom:id", "", ns) or "").strip()
		if title and url:
			results.append(SearchResult(title=title, url=url, snippet=snippet))
		if len(results) >= max_results:
			break
	return results


def _download_path(workspace_key: str, name: str, suffix: str) -> Path:
	downloads_dir = storage.category_dir(workspace_key) / "downloads"
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

	answer = result.get("answer")
	if isinstance(answer, str) and answer.strip():
		lines = [answer.strip()]
		claims = result.get("claims") or []
		if claims:
			lines.extend(["", "Citations:"])
			for claim in claims:
				ids = ", ".join(claim.get("source_ids") or []) or "unreferenced"
				lines.append(f"- [{ids}] {claim.get('text') or ''}")
		return "\n".join(lines)

	paper = result.get("fetch", {}).get("paper") or {}
	metadata = paper.get("metadata") or {}
	source = paper.get("source") or {}
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

	lines.extend(["", "The paper was downloaded and extracted, but no grounded answer was returned."])

	return "\n".join(lines)


def _web_to_text(result: dict, query: str) -> str:
	answer = result.get("answer")
	if isinstance(answer, str) and answer.strip():
		claims = result.get("claims") or []
		if not claims:
			return answer.strip()
		lines = [answer.strip(), "", "Citations:"]
		for claim in claims:
			ids = ", ".join(claim.get("source_ids") or []) or "unreferenced"
			lines.append(f"- [{ids}] {claim.get('text') or ''}")
		return "\n".join(lines)

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
		claims = result.get("claims") or []
		if not claims:
			return answer.strip()
		lines = [answer.strip(), "", "Citations:"]
		for claim in claims:
			ids = ", ".join(claim.get("source_ids") or []) or "unreferenced"
			lines.append(f"- [{ids}] {claim.get('text') or ''}")
		return "\n".join(lines)

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
	"""Render the grounded answer and the papers downloaded for it."""
	lines = ["Multi-paper research", f"Query: {query}"]
	if result.get("error"):
		return "\n".join(lines + ["", f"Error: {result['error']}"])

	answer = result.get("answer")
	if isinstance(answer, str) and answer.strip():
		lines.extend(["", answer.strip()])
	claims = result.get("claims") or []
	if claims:
		lines.extend(["", "Citations:"])
		for claim in claims:
			ids = ", ".join(claim.get("source_ids") or []) or "unreferenced"
			lines.append(f"- [{ids}] {claim.get('text') or ''}")
	lines.extend(["", f"Downloaded papers ({len(result.get('papers') or [])}):"])
	for idx, item in enumerate(result.get("papers") or [], start=1):
		paper = (item.get("result") or {}).get("fetch", {}).get("paper") or {}
		metadata = paper.get("metadata") or {}
		lines.append(f"{idx}. {metadata.get('title') or item.get('title') or 'Untitled'}")
		lines.append(f"   {item.get('url') or 'No URL'}")
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
	workspace_key = state.get("workspace_key", state["session_id"])
	_ensure_workspace(workspace_key)
	attempt = int(state.get("attempt", 0)) + 1
	reflections = list(state.get("reflections") or [])
	if attempt == 1:
		reflections = _load_reflections(workspace_key, state["text"])
	requested_mode = state.get("requested_mode")
	if attempt == 1 and requested_mode:
		# The orchestrator already chose the source type. Avoid spending another LLM call
		# rediscovering the same route; Reflexion may still choose a different route after
		# a failed first attempt.
		decision = {
			"mode": requested_mode,
			"query": state["text"],
			"workspace_ids": [],
			"section": "",
			"paper_count": int(state.get("paper_count", 1)),
			"url": (state.get("sources") or [None])[0] if requested_mode == "url" else None,
			"save_requested": requested_mode in {"paper", "paper_search", "url"},
			"should_fetch_pages": requested_mode == "web",
			"decision_reason": "caller supplied direct route",
		}
	else:
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


_WORKSPACE_ANSWER_SYSTEM_PROMPT = (
	"Answer the user's question using only the supplied records from this project's own "
	"knowledge base. These are the project's stored gaps, papers and ideas — treat them as "
	"what the project knows, not as external literature. Do not use outside knowledge and do "
	"not infer claims the records do not support. Cite the record ids you used. If the records "
	"do not answer the question, say so plainly and say what they do contain."
)


def _answer_from_workspace(question: str, text: str) -> str | None:
	if not text.strip():
		return None
	return chat(get_llm("cheap", temperature=0), _WORKSPACE_ANSWER_SYSTEM_PROMPT,
				f"Question:\n{question}\n\nRecords from the project's knowledge base:\n\n{text}").strip()


def workspace_node(state: InvestigatorState) -> dict:
	"""Read what the project already holds, and answer from it.

	The only route that touches the category workspace rather than the session folder, and
	the only one that writes nothing at all: everything it reads is already saved, so there
	is nothing to stage or promote.

	Without a category there is no workspace to read — the standalone/legacy caller passes
	only a session id — and that is reported as a failure rather than silently answered
	from nothing, so the evaluator can send the actor to the web instead.
	"""
	category_slug = _normalize(state.get("category_slug") or "")
	question = _normalize(state.get("text") or "") or state.get("query", "")
	if not category_slug:
		result = {"error": "no category workspace is attached to this investigator run, "
						   "so there is nothing of the project's own to read"}
		trace.log_event("investigator_workspace", error=result["error"])
		return {"research_result": result}

	result = workspace_lookup.lookup(
		category_slug,
		ids=list(state.get("workspace_ids") or []),
		query=state.get("query", ""),
		section=state.get("section", ""),
	)
	trace.log_event("investigator_workspace",
					category=category_slug,
					ids=list(state.get("workspace_ids") or []),
					section=state.get("section", ""),
					found=[r["id"] for r in result.get("records") or []],
					unknown=result.get("unknown_ids") or [])

	if result.get("text"):
		try:
			result["answer"] = _answer_from_workspace(question, result["text"])
		except Exception as exc:  # noqa: BLE001 - keep the records if the LLM is unavailable
			result["answer_error"] = f"{type(exc).__name__}: {exc}"
	return {"research_result": result}


def _workspace_to_text(result: dict, query: str) -> str:
	"""What the run returns to its caller.

	The answer leads and the records follow it by id. The ids matter as much as the prose:
	the caller is usually a step that will want to ask for one of these records again, and
	an answer that names no ids ends that trail.
	"""
	if error := result.get("error"):
		return f"Workspace lookup for '{query}' found nothing: {error}"
	lines = []
	if answer := result.get("answer"):
		lines.append(answer)
	elif failure := result.get("answer_error"):
		lines.append(f"(could not summarise the records: {failure})")
	if records := result.get("records"):
		lines.append("\nRead from the project's knowledge base: "
					 + ", ".join(f"{r['id']} ({r['kind']})" for r in records))
	if unknown := result.get("unknown_ids"):
		lines.append(f"Not in the workspace: {', '.join(unknown)}")
	if not result.get("answer"):
		# No answer means the LLM step did not run or failed. The records themselves are
		# still the finding, and dropping them would report an empty result over a full one.
		lines.append("\n" + result.get("text", ""))
	return "\n".join(lines).strip()


def paper_node(state: InvestigatorState) -> dict:
	workspace_key = state.get("workspace_key", state["session_id"])
	_ensure_workspace(workspace_key)
	query = (state.get("sources") or [state["query"]])[0]
	result = _fetch_extract_paper(workspace_key, query)
	answer, evidence, claims = _answer_from_papers(state.get("text") or state["query"], [result])
	result.pop("full_text", None)  # keep full text on disk, not in the run log/state
	return {"research_result": {**result, "answer": answer, "evidence": evidence, "claims": claims}}


def paper_search_node(state: InvestigatorState) -> dict:
	"""Find paper landing pages before the bounded sequential download step."""
	direct_sources = [_normalize(source) for source in (state.get("sources") or [])
				  if _normalize(source)]
	if direct_sources:
		return {"research_result": {
			"requested_count": min(len(direct_sources), state["paper_count"]),
			"search_results": [
				{"title": source, "url": source, "snippet": "direct source"}
				for source in direct_sources
			],
			"papers": [],
		}}
	search_query = f"{state['query']} research paper site:arxiv.org"
	web_error: str | None = None
	try:
		results = _search_web(search_query, max_results=max(_MAX_RESULT_COUNT, state["paper_count"] * 3))
	except Exception as exc:  # noqa: BLE001 - return a readable text error
		results = []
		web_error = f"{type(exc).__name__}: {exc}"

	if not results:
		try:
			results = _search_arxiv(state["query"], max_results=max(_MAX_RESULT_COUNT, state["paper_count"] * 3))
		except Exception as exc:  # noqa: BLE001 - preserve web failure details when both fail
			arxiv_error = f"{type(exc).__name__}: {exc}"
			if web_error is not None:
				return {"research_result": {"error": f"web_search_failed: {web_error}; arxiv_search_failed: {arxiv_error}"}}
			return {"research_result": {"error": f"arxiv_search_failed: {arxiv_error}"}}
	normalized_results: list[SearchResult] = []
	for result in results:
		canonical_url = _canonical_arxiv_url(result.url)
		if not canonical_url:
			continue
		normalized_results.append(SearchResult(title=result.title, url=canonical_url, snippet=result.snippet))
	results = _deduplicate_search_results(normalized_results)
	return {
		"research_result": {
			"requested_count": state["paper_count"],
			"search_results": [result.__dict__ for result in results],
			"papers": [],
		}
	}


def download_papers_node(state: InvestigatorState) -> dict:
	"""Fetch independent papers concurrently, preserving search order."""
	result = state["research_result"]
	if result.get("error"):
		return {}

	workspace_key = state.get("workspace_key", state["session_id"])
	_ensure_workspace(workspace_key)
	requested_count = max(1, min(int(state.get("paper_count", 1)), _MAX_PAPER_DOWNLOADS))
	# Respect the user's requested count strictly: investigate only the top-N
	# candidates instead of expanding the attempt pool.
	candidates = list(result.get("search_results") or [])[:requested_count]
	trace.log_event(
		"investigator downloads started",
		count=len(candidates),
		requested=requested_count,
		papers=[{"title": item.get("title"), "url": item.get("url")} for item in candidates],
	)

	def download_one(candidate: dict) -> dict:
		query = candidate.get("url") or candidate.get("title") or state["query"]
		paper_result = _fetch_extract_paper(workspace_key, query)
		return {
			"title": candidate.get("title"),
			"url": candidate.get("url"),
			"query": query,
			"result": paper_result,
		}

	papers: list[dict] = []
	for candidate_index in range(0, len(candidates), _MAX_PARALLEL_PAPER_DOWNLOADS):
		batch = candidates[candidate_index:candidate_index + _MAX_PARALLEL_PAPER_DOWNLOADS]
		if not batch:
			continue
		with ThreadPoolExecutor(max_workers=len(batch)) as executor:
			batch_results = list(executor.map(download_one, batch))
		papers.extend(batch_results)
		trace.log_event(
			"investigator download progress",
			completed=len(papers),
			total=len(candidates),
		)
	selected = papers[:requested_count]
	answer, evidence, answer_claims = _answer_from_papers(
		state.get("text") or state["query"], [item.get("result") or {} for item in selected],
	)
	for item in selected:
		(item.get("result") or {}).pop("full_text", None)
	trace.log_event(
		"investigator downloads finished",
		count=len(selected),
		successful=sum(
			(item.get("result") or {}).get("status") not in {"error", "needs_source"}
			for item in selected
		),
	)
	return {"research_result": {
		**result, "papers": selected, "attempted_papers": papers, "answer": answer,
		"evidence": evidence, "claims": answer_claims,
	}}


def web_node(state: InvestigatorState) -> dict:
	query = state["query"]
	session_id = state.get("workspace_key", state["session_id"])
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
	result["evidence"] = _web_evidence(result["search_results"], result["pages"])
	if result.get("answer"):
		result["claims"] = _claims_from_answer(result["answer"], result["evidence"])
	return {"research_result": result}


def url_node(state: InvestigatorState) -> dict:
	try:
		result = _scrape_url(state["url"], session_id=state.get("workspace_key", state["session_id"]), save_requested=state.get("save_requested", False))
	except Exception as exc:  # noqa: BLE001 - return a readable text error
		return {"research_result": {"error": f"{type(exc).__name__}: {exc}"}}

	try:
		answer = _answer_from_fetched_pages(state["query"], [result])
		if answer:
			result["answer"] = answer
	except Exception as exc:  # noqa: BLE001 - preserve the fetched page if the LLM is unavailable
		result["answer_error"] = f"{type(exc).__name__}: {exc}"
	result["evidence"] = _web_evidence([], [result])
	if result.get("answer"):
		result["claims"] = _claims_from_answer(result["answer"], result["evidence"])
	return {"research_result": result}


def _deterministic_evaluate(mode: str, result: dict) -> tuple[bool, str]:
	feedback = ""
	if result.get("error"):
		feedback = f"The {mode} route failed: {result['error']}"
	elif mode == "workspace" and not result.get("records"):
		feedback = ("The workspace holds nothing matching that. Try different terms, or go "
					"to the web if the answer is not something this project would have stored.")
	elif mode == "paper" and result.get("status") != "ok":
		feedback = result.get("message") or f"The paper workflow ended with status {result.get('status', 'unknown')}."
	elif mode == "paper" and not result.get("answer"):
		feedback = "The paper was fetched, but no answer was produced from its content."
	elif mode == "paper_search" and not result.get("papers"):
		feedback = "No candidate paper completed; refine the paper title/topic or use a more specific source."
	elif mode == "paper_search" and not result.get("answer"):
		feedback = "Papers were fetched, but no answer was produced from their content."
	elif mode == "web" and not result.get("answer") and not result.get("search_results"):
		feedback = "The web search returned no results; revise the search query."
	elif mode == "url" and not result.get("text"):
		feedback = "The supplied URL produced no readable content; try its canonical or downloadable form."
	return not feedback, feedback


def _fetch_failed(mode: str, result: dict) -> bool:
	"""Identify retrieval failures that cannot be improved by Reflexion."""
	if result.get("error"):
		return True
	if mode == "paper":
		return result.get("status") in {"error", "needs_source"}
	if mode == "paper_search":
		if not result.get("papers"):
			return True
		return all((item.get("result") or {}).get("status") in {"error", "needs_source"}
				   for item in result.get("papers") or [])
	if mode == "web":
		if not result.get("search_results"):
			return True
		pages = result.get("pages") or []
		return bool(pages) and all(page.get("error") for page in pages)
	return mode == "url" and not result.get("text")


def evaluate(state: InvestigatorState) -> dict:
	"""Reflexion evaluator with an LLM judgment and a deterministic fallback.

	The evaluation step now uses an LLM when available to judge whether the
	latest artifact is usable, while still falling back to the deterministic
	rule-based checks used previously when the model call fails.
	"""
	
	mode = state["mode"]
	result = state.get("research_result") or {}
	user_request = _normalize(state.get("text") or "")
	query = _normalize(state.get("query") or "")
	if ENABLE_DETERMINISTIC_FETCH_FALLBACK and _fetch_failed(mode, result):
		evaluation = {
			"success": True,
			"feedback": "Fetch failed; continuing with the deterministic partial result instead of retrying.",
			"reason": "deterministic fetch fallback",
			"attempt": state.get("attempt", 1),
		}
		trace.log_event("investigator_evaluator", **evaluation)
		return {"evaluation": evaluation}
	payload = {
		"mode": mode,
		"user_request": user_request,
		"query": query,
		"result": result,
	}

	try:
		judgment = chat_with_structured_output(
			get_llm("reasoner", temperature=0),
			_EVALUATION_SYSTEM_PROMPT,
			json.dumps(payload, ensure_ascii=False, default=str),
			InvestigatorEvaluation,
		)
		success = bool(getattr(judgment, "success", False))
		feedback = _normalize(getattr(judgment, "feedback", ""))
		reason = _normalize(getattr(judgment, "reason", ""))
	except Exception:
		success, feedback = _deterministic_evaluate(mode, result)
		reason = ""

	if not feedback and not success:
		feedback = "The latest attempt did not produce a usable result."

	evaluation = {
		"success": success,
		"feedback": feedback,
		"reason": reason,
		"attempt": state.get("attempt", 1),
	}
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
	print(f"Investigator reflection: {lesson}")
	_store_reflection(state.get("workspace_key", state["session_id"]), state["text"], lesson)
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
	if mode == "workspace":
		output = _workspace_to_text(state["research_result"], state["query"])
	elif mode == "paper":
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
	g.add_node("workspace", workspace_node)
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
							{"workspace": "workspace", "paper": "paper",
							 "paper_search": "paper_search", "web": "web", "url": "url"})
	g.add_edge("workspace", "evaluate")
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
	category_slug: str | None = None,
	mode: Literal["workspace", "paper", "paper_search", "web", "url"] | None = None,
	sources: list[str] | None = None,
	paper_count: int | None = None,
	stream: bool = True,
	echo: bool | None = None,
	max_attempts: int = _DEFAULT_MAX_ATTEMPTS,
) -> str:
	"""Route a free-text instruction to paper reading or web research.

	Returns plain text so callers can display it directly. The paper path only
	fetches and extracts sources, then answers the question from their content; it
	does not run summary, gap, director, validator, or ranking agents. The web path searches and fetches the
	top hits when the prompt asks for more than just a search result list. Every
	download is stored in ``workspace/<category>/sessions/<session_id>/`` when
	``category_slug`` is supplied by the validator.  The category-less form is
	kept for standalone callers during migration.
	"""
	text = _normalize(text)
	if not text:
		raise ValueError("Text must not be empty.")
	session_id = _workspace_component(session_id, field="session_id")
	workspace_key = _session_workspace(session_id, category_slug)

	graph = _get_graph()
	if max_attempts < 1:
		raise ValueError("max_attempts must be at least 1.")
	if paper_count is not None and not 1 <= paper_count <= _MAX_PAPER_DOWNLOADS:
		raise ValueError(f"paper_count must be between 1 and {_MAX_PAPER_DOWNLOADS}.")
	# Keep the historical graph input shape for the common/default call; the
	# evaluator supplies its own default when this optional control is absent.
	inputs = {"text": text, "session_id": session_id}
	if mode is not None:
		inputs["requested_mode"] = mode
	if sources is not None:
		inputs["sources"] = [_normalize(source) for source in sources if _normalize(source)]
	if paper_count is not None:
		inputs["paper_count"] = paper_count
	if category_slug is not None:
		inputs["workspace_key"] = workspace_key
		# The bare slug, so the workspace route can read the category's own gaps/papers/ideas.
		# Without it that route has nothing to read and says so.
		inputs["category_slug"] = category_slug
	if max_attempts != _DEFAULT_MAX_ATTEMPTS:
		inputs["max_attempts"] = max_attempts
	if not stream:
		return graph.invoke(inputs)["result"]

	if echo is None:
		echo = True
	runlog = GraphRunLog("investigator_agent", workspace=workspace_key, run_hint=text)
	runlog.run(graph, inputs, echo=echo)
	if runlog.error:
		raise RuntimeError(runlog.error)
	return runlog.output


if __name__ == "__main__":
	import sys

	prompt = " ".join(sys.argv[2:]) or "what was the name of the 11th us president?"
	session_id = sys.argv[1] if len(sys.argv) > 1 else "investigator-session"
	max_attempts = int(os.getenv("NOVADRAFT_MAX_ATTEMPTS", str(_DEFAULT_MAX_ATTEMPTS)))
	print(run(prompt, session_id=session_id, stream=False, max_attempts=max_attempts))
