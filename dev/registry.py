"""Agent + subagent + flow registry for the dev/test harness.

**Auto-discovery.** This module no longer hard-codes the agent tables. On import
it scans ``src/agents/*/`` and reads a ``MANIFEST`` from each package's
``__init__.py``. Drop a new agent folder in with a ``MANIFEST`` and it is
registered automatically — no edit to this file. A package without a ``MANIFEST``
is skipped (so half-built agents don't break the harness).

The discovered manifests are fanned out into the same tables the rest of the
harness already consumes, so ``dev/run.py``, ``dev/ui.py`` and the frontend are
unchanged:
  - AGENTS:    top-level flows the user runs (e.g. ``paper_agent``). The value is
                the manifest's ``run`` callable, wrapped so bool-typed params
                (e.g. ``force``) accept "true"/"1"/"yes" strings from the UI.
  - GRAPH_BUILDERS: agents built as LangGraph graphs, keyed name -> builder the
                dev UI streams. Set by a manifest's ``graph_builder``.
  - LOGGED_AGENTS:  graph agents that write a per-run ``.jsonl``, keyed by the
                input field holding the workspace/category slug
                (manifest ``workspace_field``).
  - AGENT_STEPS:    pipeline-order step labels, a display hint (manifest ``steps``).
  - POST_RUN:  optional per-agent hook run *after* a streamed run, on
                ``(form inputs, output)``, returning the output to record. For work
                that belongs to the caller rather than the graph — e.g. the creative
                thinker's optional persist step, which is the orchestrator's write and
                must stay outside the agent. Set by a manifest's ``post_run``.
  - EXAMPLES:   ready-made showcase runs, keyed agent -> [{name, label, description}].
                ``name`` is a file in ``dev/fixtures/``, so the same example runs
                from the CLI (``--fixture``) and from the UI's example dropdown
                (which fetches it via ``/api/fixture/<name>`` and fills the form).
  - SUBAGENTS: individual stages kept for isolated testing. Each agent declares
                its own under the manifest's ``subagents`` list.
  - INPUT_ADAPTERS: optional per-agent ``form-inputs -> graph-inputs`` transform,
                for graph agents whose state isn't directly form-typeable (e.g.
                the validator takes a ``Gap`` object). Applied by ui.py before
                ``graph.stream``. Set by a manifest's ``input_adapter``.
  - FLOWS:     multi-agent flows spanning more than one top-level agent.
  - SCHEMAS:   UI form fields, taken from the manifest's ``schema`` or, when
                omitted, auto-derived from the callable signature.

Manifest shape (everything except ``run`` is optional)::

    MANIFEST = {
        "kind": "agent",                 # "agent" | "flow"  (default "agent")
        "run": <callable(**inputs)->json>,
        "graph_builder": <callable()->compiled graph>,
        "workspace_field": "category_slug",
        "steps": ["fetch", "extract", ...],
        "input_adapter": <callable(inputs)->graph_inputs>,
        "post_run": <callable(inputs, output)->output>,
        "schema": [ {"name","type","required","placeholder"}, ... ],
        "subagents": [ {"name","run","schema"}, ... ],
        "examples": [ {"name": "<fixture>.json", "label", "description"}, ... ],
    }

``name`` is taken from the package directory, so it never has to be repeated.
"""
from __future__ import annotations

import importlib
import inspect
import sys
from pathlib import Path

# Make the package importable when running this file directly.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

AGENTS_PKG = "src.agents"
AGENTS_DIR = ROOT / "src" / "agents"


def _truthy(v) -> bool:
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in {"1", "true", "yes", "y", "on"}


# --- signature introspection ------------------------------------------------

def _params(fn):
    """Named, form-fillable params of ``fn`` (skips ``*args``/``**kwargs``)."""
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return []
    return [
        p for p in sig.parameters.values()
        if p.kind not in (p.VAR_POSITIONAL, p.VAR_KEYWORD)
    ]


def _wrap(fn):
    """Coerce bool-typed params (e.g. ``force``) from UI strings to real bools.

    Signature-driven: any param whose default is a ``bool`` gets ``_truthy``
    applied, so manifests just hand over the bare ``run`` function — no lambdas.
    """
    bool_params = {p.name for p in _params(fn) if isinstance(p.default, bool)}
    if not bool_params:
        return fn

    def wrapped(**kwargs):
        kwargs = dict(kwargs)
        for name in bool_params:
            if name in kwargs:
                kwargs[name] = _truthy(kwargs[name])
        return fn(**kwargs)

    wrapped.__name__ = getattr(fn, "__name__", "wrapped")
    return wrapped


def _auto_schema(fn) -> list[dict]:
    """Best-effort UI form derived from a callable's signature.

    Fallback for agents that don't spell out an explicit ``schema``. Required =
    no default; bools render with a ``false`` placeholder. Human-friendly
    placeholders need an explicit schema — they can't be inferred.
    """
    fields = []
    for p in _params(fn):
        fields.append({
            "name": p.name,
            "type": "text",
            "required": p.default is inspect.Parameter.empty,
            "placeholder": "false" if isinstance(p.default, bool) else "",
        })
    return fields


# --- discovery --------------------------------------------------------------

def _discover() -> list[dict]:
    """Import every ``src/agents/<pkg>/`` exposing a ``MANIFEST`` (sorted)."""
    manifests = []
    if not AGENTS_DIR.is_dir():
        return manifests
    for entry in sorted(AGENTS_DIR.iterdir()):
        if not entry.is_dir() or entry.name.startswith((".", "_")):
            continue
        if not (entry / "__init__.py").exists():
            continue
        try:
            mod = importlib.import_module(f"{AGENTS_PKG}.{entry.name}")
        except Exception as e:  # noqa: BLE001 — one bad agent shouldn't kill the harness
            print(f"[registry] skipping {entry.name}: {type(e).__name__}: {e}",
                  file=sys.stderr)
            continue
        manifest = getattr(mod, "MANIFEST", None)
        if not isinstance(manifest, dict):
            continue
        manifests.append({"name": entry.name, **manifest})

    # Also discover the top-level graphs in ``src/graph`` (compose the agents
    # above into end-to-end graphs — see src/graph/pipeline.py): the full
    # ``pipeline`` and the ``resume`` tail.
    if (ROOT / "src" / "graph" / "__init__.py").exists():
        try:
            gmod = importlib.import_module("src.graph")
            for name, attr in (("pipeline", "MANIFEST"), ("resume", "MANIFEST_RESUME")):
                gmanifest = getattr(gmod, attr, None)
                if isinstance(gmanifest, dict):
                    manifests.append({"name": name, **gmanifest})
        except Exception as e:  # noqa: BLE001 — don't let it kill the harness
            print(f"[registry] skipping src/graph: {type(e).__name__}: {e}",
                  file=sys.stderr)

    return manifests


AGENTS: dict = {}
GRAPH_BUILDERS: dict = {}
LOGGED_AGENTS: dict = {}
AGENT_STEPS: dict = {}
EXAMPLES: dict = {}
POST_RUN: dict = {}
SUBAGENTS: dict = {}
INPUT_ADAPTERS: dict = {}
FLOWS: dict = {}
SCHEMAS: dict = {"agent": {}, "subagent": {}, "flow": {}}


def _register(manifest: dict) -> None:
    name = manifest["name"]
    kind = manifest.get("kind", "agent")
    run_fn = manifest.get("run")
    table = FLOWS if kind == "flow" else AGENTS
    schema_bucket = "flow" if kind == "flow" else "agent"

    if run_fn is not None:
        table[name] = _wrap(run_fn)
    if manifest.get("graph_builder"):
        GRAPH_BUILDERS[name] = manifest["graph_builder"]
    if manifest.get("workspace_field"):
        LOGGED_AGENTS[name] = manifest["workspace_field"]
    if manifest.get("steps"):
        AGENT_STEPS[name] = manifest["steps"]
    if manifest.get("examples"):
        EXAMPLES[name] = manifest["examples"]
    if manifest.get("post_run"):
        POST_RUN[name] = manifest["post_run"]
    if manifest.get("input_adapter"):
        INPUT_ADAPTERS[name] = manifest["input_adapter"]
    SCHEMAS[schema_bucket][name] = manifest.get("schema") or (
        _auto_schema(run_fn) if run_fn else []
    )

    for sub in manifest.get("subagents", []):
        sub_run = sub["run"]
        SUBAGENTS[sub["name"]] = _wrap(sub_run)
        SCHEMAS["subagent"][sub["name"]] = sub.get("schema") or _auto_schema(sub_run)


for _m in _discover():
    _register(_m)


def list_all() -> dict:
    return {
        "agents": sorted(AGENTS),
        "subagents": sorted(SUBAGENTS),
        "flows": sorted(FLOWS),
        "schemas": SCHEMAS,
        "agent_steps": AGENT_STEPS,
        "logged_agents": LOGGED_AGENTS,
        "examples": EXAMPLES,
    }
