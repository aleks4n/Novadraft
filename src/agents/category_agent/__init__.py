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
            "name": "topic",
            "type": "text",
            "required": True,
            "placeholder": (
                "z.B Mahine Learning, Natural Language Processing, Computer Vision, etc."
            ),
        },
    ],
}