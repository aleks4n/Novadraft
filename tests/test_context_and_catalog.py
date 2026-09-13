"""What a step can see: the workspace index, and how loaded records reach the thinker.

Covers that the thinker's context renderer keeps each record type's own material (a
gap's evidence quote, a paper's summary), and that the catalog tells a step what
the workspace holds beyond its own slice.
"""
from __future__ import annotations

import json

import pytest

from src.agents.creative_thinker.tools import context as thinker_context
from src.common.schemas.context import Context
from src.common.tools import catalog
from src.common.utils import storage


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """A small category: two gaps in different stages, one paper, one idea."""
    monkeypatch.setattr(storage, "workspace_root", lambda: tmp_path)
    # The catalog memoises entries by (path, mtime, size), and tmp_path reuses names
    # across tests — so a stale entry from an earlier test could otherwise be served.
    catalog._ENTRY_CACHE.clear()

    root = tmp_path / "cat"
    (root / "gaps" / "master").mkdir(parents=True)
    (root / "gaps" / "archive").mkdir(parents=True)
    (root / "papers" / "arxiv_1").mkdir(parents=True)
    (root / "ideas").mkdir(parents=True)
    (root / "meta.json").write_text("{}")

    (root / "gaps" / "master" / "gap_live.json").write_text(json.dumps({
        "gap_id": "gap_live", "title": "Attention is quadratic", "type": "limitation",
        "canonical_description": "cost is quadratic in sequence length",
        "description": "The cost grows with the square of the sequence length.",
        "evidence": [{"paper_id": "arxiv_1", "section": "results",
                      "anchor": "complexity is quadratic",
                      "quote": "complexity is quadratic in n"}],
        "verdict": {"gap_id": "gap_live", "status": "open", "confidence": 0.9,
                    "reasoning": "no prior work closes it"},
        "extracted_at": "2026-01-01T00:00:00Z",
    }))
    (root / "gaps" / "archive" / "gap_old.json").write_text(json.dumps({
        "gap_id": "gap_old", "title": "Settled already", "type": "caveat",
        "canonical_description": "already closed", "description": "closed",
        "evidence": [], "extracted_at": "2026-01-01T00:00:00Z",
    }))
    (root / "papers" / "arxiv_1" / "paper.json").write_text(json.dumps({
        "paper_id": "arxiv_1",
        "metadata": {"title": "A Paper", "year": 2017},
        "summary": {"keywords": ["attention"],
                    "problem": {"statement": "sequential computation is slow"},
                    "approach": {"assumptions": ["positions are independent"]},
                    "open_threads": ["extend to other modalities"]},
    }))
    (root / "ideas" / "idea_1.json").write_text(json.dumps({
        "idea_id": "idea_1", "title": "Linear attention", "one_liner": "make it linear",
    }))
    return "cat"


def test_catalog_indexes_everything_live_stages_first(workspace):
    index = catalog.build(workspace)

    assert index["counts"] == {"gaps": 2, "papers": 1, "ideas": 1}
    # `master` outranks `archive`: a ceiling must cut retired gaps before live ones.
    assert [entry["id"] for entry in index["gaps"]] == ["gap_live", "gap_old"]
    assert index["papers"][0]["title"] == "A Paper"
    # A pointer, not a record — no body field is carried at all.
    assert set(index["gaps"][0]) == {"kind", "id", "title", "marker", "type"}


def test_catalog_excludes_what_is_already_loaded(workspace):
    """A record must not be both the subject of the turn and a line in the index."""
    index = catalog.build(workspace, exclude_ids={"gap_live"})

    assert [entry["id"] for entry in index["gaps"]] == ["gap_old"]
    assert index["counts"]["gaps"] == 1


def test_catalog_ceiling_reports_what_it_dropped(workspace, monkeypatch):
    monkeypatch.setattr(catalog, "MAX_GAPS", 1)

    index = catalog.build(workspace)

    assert len(index["gaps"]) == 1
    assert index["counts"]["gaps"] == 2  # the true total, not the truncated length
    assert index["omitted"]["gaps"] == 1


def test_thinker_sees_gap_evidence_and_the_whole_paper_summary(workspace):
    """The regression this replaces: both were dropped by the generic renderer."""
    _stage, raw_gap = storage.find_gap(workspace, "gap_live")
    ctx = Context(
        gaps=[raw_gap],
        papers=[{"paper_id": "arxiv_1", "title": "A Paper", "year": 2017,
                 "description": "a one-line summary",
                 "summary": (storage.read_paper_json(workspace, "arxiv_1") or {})["summary"]}],
        catalog=catalog.build(workspace, exclude_ids={"gap_live", "arxiv_1"}),
    )

    rendered = thinker_context.render_context(ctx.model_dump(mode="json"))

    # The gap: not just its claim, but the sentence in the paper it rests on.
    assert "complexity is quadratic in n" in rendered
    assert "verdict: open" in rendered
    # The paper: the structured summary, which the old renderer replaced with
    # `description` alone and never showed.
    assert "sequential computation is slow" in rendered
    assert "positions are independent" in rendered
    assert "extend to other modalities" in rendered
    # The index comes last, so it cannot be mistaken for loaded material.
    assert rendered.index("Gaps in scope") < rendered.index("Also in the workspace")
    assert "gap_old" in rendered


def test_thinker_context_omits_the_catalog_when_it_is_empty():
    rendered = thinker_context.render_context(Context().model_dump(mode="json"))

    assert "Also in the workspace" not in rendered
    assert rendered == "_(no context loaded)_"


def test_unrecognised_context_keys_still_render():
    """A plan that loads something new must not be silently dropped."""
    rendered = thinker_context.render_context(
        {"discussion": [{"id": "d1", "text": "we argued about scale"}]})

    assert "we argued about scale" in rendered


def test_handover_event_records_what_the_thinker_was_given(workspace, monkeypatch):
    """The run must be able to answer "did that step even have the paper?".

    Nothing recorded it before: the step log carried the tool name and the planner's
    reason, both of which describe what was asked for rather than what was in scope.
    """
    from src.agents.creative_thinker.schemas import Budget, ThinkerCall
    from src.agents.orchestrator_agent.tools import creative_thinker as tool
    from src.common.schemas.idea import IdeaRecord

    captured: list[dict] = []
    monkeypatch.setattr(tool.trace, "emit", captured.append)

    _stage, raw_gap = storage.find_gap(workspace, "gap_live")
    around = Context(gaps=[raw_gap],
                     papers=[{"paper_id": "arxiv_1", "title": "A Paper"}],
                     catalog=catalog.build(workspace, exclude_ids={"gap_live", "arxiv_1"}))
    call = ThinkerCall(mode="generation", idea=IdeaRecord(),
                       context=around.model_dump(mode="json"),
                       task="ideas for this gap", budget=Budget())

    tool._trace_handover(call, IdeaRecord(), around)

    assert len(captured) == 1
    data = captured[0]["data"]
    assert captured[0]["name"] == "context handed to creative thinker"
    assert data["inventory"]["gaps"] == ["gap_live"]
    assert data["inventory"]["papers"] == ["arxiv_1"]
    assert data["inventory"]["catalog_counts"]["gaps"] == 1
    # The literal prompt text, not just counts: "1 gap, 1 paper" reads as sufficient
    # context right up until you see what the paper block actually said.
    assert "complexity is quadratic in n" in data["context_md"]
    assert data["context_chars"] == len(data["context_md"])
