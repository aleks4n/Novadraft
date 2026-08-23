"""PaperExtractAgent: parse raw paper files into structured sections.

Reads files written by PaperFetchAgent (tex tree or PDF), produces a flat
section map + bibliography + tables, writes them back into paper.json.
No LLM calls. Idempotent: re-running on an already-extracted paper is a no-op
unless force=True.
"""
from .extract import extract

__all__ = ["extract"]
