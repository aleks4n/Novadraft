"""Run logging — the two halves of one pipeline, kept as separate modules.

- :mod:`trace` — the producer side. ``emit`` / ``log_event``, called from inside a
  running graph node, push a fine-grained leaf into LangGraph's custom stream.
  Lightweight (only depends on ``langgraph.config``) so the deepest leaf code —
  tools, fetchers, the LLM registry — can import it freely.
- :mod:`runlog` — the consumer / orchestrator side. ``GraphRunLog`` drives the
  whole run: streams the graph, normalizes each chunk (``RunRecorder``), writes the
  flat ``.jsonl``, and replays it (``read_run``). Heavier (pulls in ``storage``);
  imported only by entrypoints.

This ``__init__`` is intentionally empty (no re-exports): importing
``...logging.trace`` must not transitively pull in ``runlog``'s dependencies, so
import the specific submodule you need.
"""
