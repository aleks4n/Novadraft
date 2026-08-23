"""Shared evidence and citation models for tool-driven research."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Evidence(BaseModel):
	"""A source artifact returned by any investigator tool."""

	source_id: str
	title: str = "Untitled source"
	url: str | None = None
	source_type: Literal["paper", "web", "pdf", "local"]
	text: str = ""
	snippet: str = ""
	fetched: bool = False
	credibility: float = Field(default=0.0, ge=0.0, le=1.0)
	error: str | None = None


class CitationClaim(BaseModel):
	"""A synthesized claim and the evidence that supports it."""

	text: str
	source_ids: list[str] = Field(default_factory=list)
	confidence: Literal["high", "medium", "low"] = "low"
	verified: bool = False


class CitedAnswer(BaseModel):
	"""Structured answer retained alongside the investigator's text output."""

	answer: str = ""
	claims: list[CitationClaim] = Field(default_factory=list)
