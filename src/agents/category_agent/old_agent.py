"""DEPRECATED — the original category agent (kept for reference, not wired in).

This was a plain Wikipedia-dossier fetcher: given a topic it wrote a Markdown
background file to ``workspace/knowledge/``. It was neither a LangGraph agent nor
LLM-backed, and it never created a category workspace. Superseded by ``agent.py``,
which rebuilds this as a streamed LangGraph that ends by writing the category's
``meta.json`` (name + short description + seminal papers). The Wikipedia fetch
helpers here are reused by the new ``research`` node — see ``agent.py``.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List
from urllib.parse import quote

import requests


WIKIPEDIA_API_URL = "https://en.wikipedia.org/w/api.php"
WIKIPEDIA_SUMMARY_URL = "https://en.wikipedia.org/api/rest_v1/page/summary/{title}"


@dataclass
class PageInfo:
	title: str
	url: str
	description: str
	summary: str


def _slugify(value: str) -> str:
	cleaned = "".join(ch.lower() if ch.isalnum() else "_" for ch in value.strip())
	while "__" in cleaned:
		cleaned = cleaned.replace("__", "_")
	return cleaned.strip("_") or "topic"


def _fetch_page_summary(title: str, timeout_seconds: int = 20) -> PageInfo:
	encoded_title = quote(title.replace(" ", "_"), safe="")
	response = requests.get(
		WIKIPEDIA_SUMMARY_URL.format(title=encoded_title),
		timeout=timeout_seconds,
		headers={"User-Agent": "novadraft-category-agent/1.0"},
	)
	response.raise_for_status()
	payload = response.json()

	page_url = payload.get("content_urls", {}).get("desktop", {}).get("page", "")
	description = payload.get("description", "")
	summary = payload.get("extract", "")
	page_title = payload.get("title", title)

	return PageInfo(
		title=page_title,
		url=page_url,
		description=description,
		summary=summary,
	)


def _fetch_related_titles(topic: str, limit: int = 8, timeout_seconds: int = 20) -> List[str]:
	params = {
		"action": "opensearch",
		"search": topic,
		"limit": max(limit + 1, 2),
		"namespace": 0,
		"format": "json",
	}
	response = requests.get(
		WIKIPEDIA_API_URL,
		params=params,
		timeout=timeout_seconds,
		headers={"User-Agent": "novadraft-category-agent/1.0"},
	)
	response.raise_for_status()
	payload = response.json()

	if not isinstance(payload, list) or len(payload) < 2:
		return []

	titles = payload[1] if isinstance(payload[1], list) else []
	topic_lower = topic.strip().lower()

	unique_titles: List[str] = []
	for title in titles:
		if not isinstance(title, str):
			continue
		if title.strip().lower() == topic_lower:
			continue
		if title not in unique_titles:
			unique_titles.append(title)
		if len(unique_titles) >= limit:
			break
	return unique_titles


def _render_knowledge_text(topic: str, main_page: PageInfo, related_pages: List[PageInfo]) -> str:
	timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
	lines = [
		"# Topic Background Dossier",
		"",
		f"- Requested topic: {topic}",
		f"- Generated: {timestamp}",
		"- Source: Wikipedia (public encyclopedia)",
		"",
		"## Primary Page",
		"",
		f"- Title: {main_page.title}",
		f"- URL: {main_page.url}",
	]

	if main_page.description:
		lines.append(f"- Description: {main_page.description}")
	lines.extend(
		[
			"",
			"### Summary",
			"",
			main_page.summary or "No summary text available.",
			"",
			"## Related Pages",
			"",
		]
	)

	if not related_pages:
		lines.append("No related pages were found.")
	else:
		for idx, page in enumerate(related_pages, start=1):
			lines.extend(
				[
					f"### {idx}. {page.title}",
					"",
					f"- URL: {page.url}",
				]
			)
			if page.description:
				lines.append(f"- Description: {page.description}")
			lines.append("")
			lines.append("Summary:")
			lines.append(page.summary or "No summary text available.")
			lines.append("")

	lines.extend(
		[
			"## Usage Note",
			"",
			"This file is designed to be attached as grounding context for an LLM.",
			"Cite the listed URLs when factual precision matters.",
		]
	)
	return "\n".join(lines)


def build_topic_background_file(topic: str, output_file: str | None = None, related_limit: int = 6) -> str:
	topic = topic.strip()
	if not topic:
		raise ValueError("Topic must not be empty.")

	main_page = _fetch_page_summary(topic)
	related_titles = _fetch_related_titles(main_page.title, limit=related_limit)

	related_pages: List[PageInfo] = []
	for title in related_titles:
		try:
			related_pages.append(_fetch_page_summary(title))
		except requests.RequestException:
			# Skip pages that fail to resolve so one broken page does not stop the run.
			continue

	text_output = _render_knowledge_text(topic=topic, main_page=main_page, related_pages=related_pages)

	if output_file:
		out_path = Path(output_file)
	else:
		out_dir = Path("workspace") / "knowledge"
		out_path = out_dir / f"{_slugify(topic)}_wikipedia_background.md"

	out_path.parent.mkdir(parents=True, exist_ok=True)
	out_path.write_text(text_output, encoding="utf-8")
	return str(out_path)


def main(topic: str) -> str:
	return build_topic_background_file(topic=topic)


def _parse_args() -> argparse.Namespace:
	parser = argparse.ArgumentParser(
		description="Create a Wikipedia-based background text file for a topic."
	)
	parser.add_argument("topic", nargs="+", help="Topic to collect (example: machine learning)")
	parser.add_argument(
		"--output",
		dest="output_file",
		default=None,
		help="Optional output file path. Defaults to workspace/knowledge/<topic>_wikipedia_background.md",
	)
	parser.add_argument(
		"--related-limit",
		type=int,
		default=6,
		help="How many related pages to include (default: 6)",
	)
	return parser.parse_args()


def run(topic: str) -> str:
    return main(topic)


if __name__ == "__main__":
	args = _parse_args()
	topic_value = " ".join(args.topic)
	output_path = build_topic_background_file(
		topic=topic_value,
		output_file=args.output_file,
		related_limit=args.related_limit,
	)
	print(output_path)
