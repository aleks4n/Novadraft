"""Tiny stdlib HTTP helpers — no external deps."""
from __future__ import annotations

import gzip
import json as _json
import random
import tarfile
import time
import urllib.error
import urllib.parse
import urllib.request
from io import BytesIO
from pathlib import Path

from ...utils.ratelimit import defer, wait_turn

_UA = "novadraft-paper-fetch/0.1 (+research; mailto:noreply@example.com)"
DEFAULT_TIMEOUT = 30
_RETRY_STATUSES = {429, 500, 502, 503, 504}


def _is_arxiv(url: str) -> bool:
    host = urllib.parse.urlparse(url).hostname or ""
    return host == "arxiv.org" or host.endswith(".arxiv.org")


def _open(url: str, timeout: int = DEFAULT_TIMEOUT, accept: str | None = None,
          retries: int = 2, backoff: float = 2.0):
    """Open an interactive fetch with bounded retry latency.

    Two retries still absorb transient failures, while avoiding the former 4+8+16+32s
    wait on one provider. Higher-level fetchers can then fail over to another source.
    """
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    if accept:
        req.add_header("Accept", accept)
    delay = backoff
    last_err: Exception | None = None
    for attempt in range(retries + 1):
        try:
            if _is_arxiv(url):
                # every arXiv request (metadata / PDF / e-print) goes through the shared
                # gate, so the ~1-per-3s spacing holds across fetchers, gaps and threads
                wait_turn("arxiv")
            return urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code in _RETRY_STATUSES and attempt < retries:
                retry_after = e.headers.get("Retry-After") if e.headers else None
                try:
                    cooldown = float(retry_after) if retry_after is not None else delay
                except (TypeError, ValueError):
                    cooldown = delay
                cooldown += random.uniform(0, min(1.0, cooldown * 0.1))
                if _is_arxiv(url):
                    defer("arxiv", cooldown)
                else:
                    time.sleep(cooldown)
                delay *= 2
                continue
            raise
        except (urllib.error.URLError, TimeoutError) as e:
            last_err = e
            if attempt < retries:
                time.sleep(delay)
                delay *= 2
                continue
            raise
    raise last_err  # unreachable


def get_bytes(url: str, timeout: int = DEFAULT_TIMEOUT) -> tuple[bytes, str]:
    """Return (body, content_type)."""
    with _open(url, timeout=timeout) as r:
        return r.read(), r.headers.get("Content-Type", "")


def get_text(url: str, timeout: int = DEFAULT_TIMEOUT) -> str:
    body, _ = get_bytes(url, timeout=timeout)
    return body.decode("utf-8", errors="replace")


def get_json(url: str, timeout: int = DEFAULT_TIMEOUT) -> dict:
    with _open(url, timeout=timeout, accept="application/json") as r:
        return _json.loads(r.read().decode("utf-8"))


def download_to(url: str, dest: Path, timeout: int = DEFAULT_TIMEOUT) -> Path:
    body, _ = get_bytes(url, timeout=timeout)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(body)
    return dest


def extract_archive(body: bytes, dest_dir: Path) -> bool:
    """Extract a tar.gz / .tar / .gz blob into dest_dir.

    Returns True on success, False if the blob is not an archive we recognize.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    # Try tar (gzipped or plain)
    try:
        with tarfile.open(fileobj=BytesIO(body)) as tf:
            tf.extractall(dest_dir, filter="data")
        return True
    except (tarfile.TarError, EOFError):
        pass
    # Try single-file gzip (arXiv sometimes ships a single .tex.gz)
    try:
        decoded = gzip.decompress(body)
        (dest_dir / "main.tex").write_bytes(decoded)
        return True
    except (OSError, EOFError):
        pass
    return False
