"""Common paper-fetch tool — fetch a paper into a workspace destination from a
single string.

Public entry point: ``fetch(destination, query)``. Does not log to
fetch_log.jsonl itself; callers log via ``storage.log_fetch(destination,
**result["log_entry"])`` (or ``**err.log_entry`` on a raised error).
"""
from .errors import PaperAlreadyExistsError, PaperFetchError
from .fetch import fetch

__all__ = ["fetch", "PaperAlreadyExistsError", "PaperFetchError"]
