"""Errors raised by the paper fetch agent."""
from __future__ import annotations


class PaperAlreadyExistsError(Exception):
    """Raised when the resolved paper already exists in the workspace."""

    def __init__(self, destination: str, paper_id: str):
        self.destination = destination
        self.paper_id = paper_id
        super().__init__(
            f"Paper '{paper_id}' already exists in workspace '{destination}'."
        )


class PaperFetchError(Exception):
    """Raised when a paper could not be fetched.

    Attributes:
        query: the original input string
        attempts: list of (source, reason) tuples describing what was tried
    """

    def __init__(self, query: str, attempts: list[tuple[str, str]]):
        self.query = query
        self.attempts = attempts
        details = "; ".join(f"{src}: {reason}" for src, reason in attempts) or "no attempts"
        super().__init__(f"Could not fetch paper for query {query!r}. Tried: {details}")
