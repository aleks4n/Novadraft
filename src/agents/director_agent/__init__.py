from .agent import run

__all__ = ["run", "MANIFEST"]

MANIFEST = {
    "kind": "agent",
    "run": run,
    "workspace_field": None,
    "steps": [
        "load_master",
        "collect_gaps",
        "deduplicate",
        "merge_evidence",
        "write_master",
    ],
    "schema": [
        {
            "name": "paper_json_paths",
            "type": "text",
            "required": True,
            "placeholder": (
                "workspace/transformers/papers/arxiv_1706.03762/paper.json\n"
                "workspace/transformers/papers/arxiv_1810.04805/paper.json"
            ),
        },
    ],
}