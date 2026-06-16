"""Tiny prompt loader.

Each LLM sub-agent keeps its system prompt in a sibling `prompt.md` so non-coders
can tune wording without touching Python. The agent loads it with
`load_prompt(__file__)`.
"""

from __future__ import annotations

from pathlib import Path


def load_prompt(module_file: str, name: str = "prompt.md", **format_args) -> str:
    """Read a prompt file and optionally format placeholders."""
    text = (Path(module_file).resolve().parent / name).read_text(encoding="utf-8")
    return text.format(**format_args) if format_args else text
