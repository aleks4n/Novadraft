import urllib.error

from src.common.tools.paper_fetch import http_util
from src.graph import pipeline


class _Response:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def test_arxiv_429_defers_every_caller_and_retries(monkeypatch):
    calls = 0
    cooldowns = []

    def urlopen(_request, timeout):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise urllib.error.HTTPError(
                "https://export.arxiv.org/api/query", 429, "rate limited",
                {"Retry-After": "12"}, None,
            )
        return _Response()

    monkeypatch.setattr(http_util.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(http_util, "wait_turn", lambda _service: None)
    monkeypatch.setattr(http_util, "defer", lambda service, seconds: cooldowns.append((service, seconds)))
    monkeypatch.setattr(http_util.random, "uniform", lambda _a, _b: 0.0)

    response = http_util._open("https://export.arxiv.org/api/query", retries=1)

    assert isinstance(response, _Response)
    assert calls == 2
    assert cooldowns == [("arxiv", 12.0)]


def test_parallel_drain_returns_failed_gap_ids(monkeypatch):
    monkeypatch.setattr(pipeline.claims, "release", lambda *_args: None)
    monkeypatch.setattr(pipeline.claims, "release_all", lambda *_args: None)
    monkeypatch.setattr(pipeline.trace, "log_event", lambda *_args, **_kwargs: None)

    def worker(gap_id):
        if gap_id == "gap_bad":
            raise RuntimeError("arXiv returned HTTP 429")
        return {"gap_id": gap_id}

    records, failed = pipeline._drain_parallel(
        "topic", ["gap_ok", "gap_bad"], worker, "validate",
    )

    assert records == [{"gap_id": "gap_ok"}]
    assert failed == ["gap_bad"]


def test_collect_marks_an_incomplete_pipeline_partial():
    result = pipeline.collect({
        "paper_id": "arxiv_1234.5678",
        "novel_gap_ids": ["gap_ok", "gap_bad"],
        "verdicts": [{"gap_id": "gap_ok"}],
        "ranked": [],
        "failed_validation_ids": ["gap_bad"],
    })["result"]

    assert result["status"] == "partial"
    assert result["failed_validation_ids"] == ["gap_bad"]
    assert "Resume gaps" in result["error"]
