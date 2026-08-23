from pathlib import Path

import pytest

from src.common.tools.paper_fetch.errors import PaperFetchError
from src.common.tools.paper_fetch.fetchers import pdf_url


def test_pdf_url_rejects_html_response(monkeypatch):
	monkeypatch.setattr(pdf_url, "get_bytes", lambda url: (b"<!DOCTYPE html>", "text/html"))

	with pytest.raises(PaperFetchError, match="not a PDF"):
		pdf_url.fetch_pdf_url("https://example.com/paper", "session", "url", "paper")


def test_pdf_url_accepts_pdf_signature(monkeypatch, tmp_path):
	monkeypatch.setattr(pdf_url, "get_bytes", lambda url: (b"%PDF-1.7\n", "application/pdf"))
	monkeypatch.setattr(pdf_url, "extract_pdf_identifiers", lambda path: {})
	monkeypatch.setattr(pdf_url, "extract_pdf_metadata", lambda path: {})
	monkeypatch.setattr(pdf_url.storage, "paper_dir", lambda destination, paper_id: tmp_path / paper_id)
	monkeypatch.setattr(pdf_url.storage, "new_paper_record", lambda paper_id, input_type, query: {
		"paper_id": paper_id,
		"metadata": {"title": None, "authors": [], "year": None},
		"source": {"resolved_via": None, "source_url": None, "files": {"pdf": None}},
		"warnings": [],
	})
	monkeypatch.setattr(pdf_url.storage, "write_paper_json", lambda destination, record: None)

	result = pdf_url.fetch_pdf_url("https://example.com/paper.pdf", "session", "url", "paper")

	assert result["source"]["files"]["pdf"] == "source.pdf"
	assert (tmp_path / result["paper_id"] / "source.pdf").read_bytes().startswith(b"%PDF-")