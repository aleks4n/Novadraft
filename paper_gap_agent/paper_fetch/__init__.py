"""PaperFetchAgent — fetch a paper into a category workspace from a single string.

Public entry point: ``fetch(category_slug, query)``.
"""
from .fetch import fetch

__all__ = ["fetch"]
