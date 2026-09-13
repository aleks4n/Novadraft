"""Shared merge logic for the critic-side growing tracked-item list, used by both the
summarize and gap critics for their ``issues`` and ``missing_hints`` lists.

The persisted list never shrinks — an item is only appended or flipped to
``resolved``. Each round the critic LLM sees the currently-open items (with their
``id``s) and returns, for this round only: items it is re-confirming as still open
(by echoing back their ``id``) and brand-new items (no ``id``).
:func:`merge_items` reconciles that single-round verdict into the persisted list:
anything previously open that was not re-confirmed this round is marked resolved;
anything new is appended with a fresh id.
"""
from __future__ import annotations

import uuid


def merge_items(persisted: list[dict], llm_items: list[dict], round_num: int,
                fields: list[str]) -> list[dict]:
    """Mutates and returns `persisted`.

    `llm_items`: this round's raw critic output, each either `{"id": <existing
    id>, ...fields}` (re-confirming an open item) or `{"id": None, ...fields}`
    (a newly-found item).
    `fields`: the item-type-specific fields to copy verbatim — e.g.
    `["field", "severity", "description", "evidence_section"]` for summary
    issues, `["gap_id", "severity", "reason"]` for gap issues, `["hint"]`
    for either's missing-hints list.
    """
    by_id = {item["id"]: item for item in persisted}
    prior_open_ids = {item["id"] for item in persisted if not item["resolved"]}
    confirmed_ids: set[str] = set()

    for raw in llm_items:
        item_id = raw.get("id")
        if item_id and item_id in by_id:
            confirmed_ids.add(item_id)
            rec = by_id[item_id]
            rec["reconfirm_count"] = rec.get("reconfirm_count", 0) + 1
            for field in fields:
                if field in raw:
                    rec[field] = raw[field]
        else:
            new_rec = {
                "id": uuid.uuid4().hex[:10],
                "resolved": False,
                "created_round": round_num,
                "resolved_round": None,
                "reconfirm_count": 0,
            }
            for field in fields:
                if field in raw:
                    new_rec[field] = raw[field]
            persisted.append(new_rec)

    for item_id in prior_open_ids - confirmed_ids:
        rec = by_id[item_id]
        rec["resolved"] = True
        rec["resolved_round"] = round_num

    return persisted


def open_items(items: list[dict]) -> list[dict]:
    return [i for i in items if not i["resolved"]]
