"""Common paper-fetch tool — fetch a paper into a workspace destination from a
single string.

Public entry point: ``fetch(destination, query)``.
"""
from .errors import PaperAlreadyExistsError, PaperFetchError
from .fetch import fetch

__all__ = ["fetch", "PaperAlreadyExistsError", "PaperFetchError"]
