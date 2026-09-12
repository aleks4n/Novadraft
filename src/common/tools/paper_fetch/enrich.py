"""Venue + citation enrichment for paper.json — Semantic Scholar, OpenAlex fallback.

Why this exists: the arXiv fetcher can only report a venue from `arxiv:journal_ref`,
which authors add by hand after journal publication. Across a real workspace that field
is set on ~6% of papers, so `metadata.venue` was almost always None and the ranking
agent's impact score fell back to its neutral prior for every gap — see
`ranking_agent/tools/score_impact.py`.

Semantic Scholar resolves an arXiv id straight to the *published* venue and a citation
count (e.g. ARXIV:2305.10601 -> "Neural Information Processing Systems", 4767 citations),
and its `publicationVenue` carries both the venue `type` and the acronyms. That matters
twice over:

- `alternate_names` gives the CORE acronym directly, so the LLM name->acronym resolver
  becomes a fallback rather than the only path.
- `type` distinguishes a real conference from a workshop or journal. Resolving the raw
  string was actively dangerous here: "4th Deployable AI Workshop at AAAI 2026" contains
  a flagship acronym, and scoring it as AAAI would hand a workshop paper an A* rating.

No API key is required. A free key (SEMANTIC_SCHOLAR_API_KEY) lifts S2 out of its shared
pool, but without one the batch endpoint still succeeds about a third of the time and
rejects the rest in well under a second — so many cheap retries get through, and because
one batch request covers up to 500 papers there are very few requests to make. The retry
budget below is sized from that measured rate.

OpenAlex is the fallback and needs no key at all (it answered every probe). It is weaker
on venues: keyed by the arXiv DOI it returns the *preprint* record, whose venue is arXiv
itself, so a venue is taken from it only when the record is not a preprint. Its citation
counts also trail S2's, but a real count beats none.

Everything here is best-effort: any failure leaves the record untouched rather than
failing the fetch, since a paper without a venue is still a usable paper.
"""
from __future__ import annotations

import datetime as dt
import os
from typing import Iterable, Optional

from .http_util import get_json, post_json

_S2_BATCH = "https://api.semanticscholar.org/graph/v1/paper/batch"
_S2_FIELDS = "externalIds,venue,publicationVenue,year,citationCount,influentialCitationCount"
_OPENALEX_WORK = "https://api.openalex.org/works"

# S2 caps a batch at 500 ids per request.
_S2_BATCH_LIMIT = 500

# Unauthenticated S2 rejects ~2 of 3 attempts (measured), but cheaply and with no
# Retry-After. Many capped retries beat few exponential ones: 12 attempts at ~30% each
# clears 98%, and a whole workspace is one or two batch requests. With a key the limit is
# a real ~1 req/s quota, so back off less aggressively and retry less.
_S2_RETRIES_KEYLESS, _S2_RETRIES_KEYED = 12, 4
_S2_MAX_BACKOFF = 6.0

# Venue names that are repositories rather than publication venues: S2 reports these for
# papers that never appeared anywhere else, and treating them as a venue would imply a
# peer-reviewed venue the paper does not have.
_NON_VENUES = {"arxiv.org", "arxiv", "ssrn", "biorxiv.org", "medrxiv.org", "research square"}


def _api_key() -> Optional[str]:
    return os.getenv("SEMANTIC_SCHOLAR_API_KEY") or None


def _api_key_headers() -> dict[str, str]:
    """S2 works unauthenticated but shares one heavily-throttled pool; a free key
    (SEMANTIC_SCHOLAR_API_KEY) raises the limit to roughly 1 req/s."""
    key = _api_key()
    return {"x-api-key": key} if key else {}


def _s2_retries() -> int:
    return _S2_RETRIES_KEYED if _api_key() else _S2_RETRIES_KEYLESS


def _mailto() -> str:
    """OpenAlex's polite pool wants a contact address; it degrades to the common pool
    without one."""
    return os.getenv("OPENALEX_MAILTO", "")


def s2_id_for(metadata: dict) -> Optional[str]:
    """The best Semantic Scholar lookup key for a paper record's identifiers.

    arXiv first: it is the identifier this pipeline almost always has, and S2 resolves it
    to the published version. DOI second. Title search is deliberately not used — a wrong
    match would attach another paper's citation count.
    """
    ids = metadata.get("identifiers") or {}
    if ids.get("arxiv_id"):
        return f"ARXIV:{ids['arxiv_id']}"
    if ids.get("doi"):
        return f"DOI:{ids['doi']}"
    if ids.get("semantic_scholar_id"):
        return str(ids["semantic_scholar_id"])
    return None


def _clean_venue(name: Optional[str]) -> Optional[str]:
    if not name or name.strip().lower() in _NON_VENUES:
        return None
    return name.strip()


def _acronyms(pv: dict, venue: Optional[str]) -> list[str]:
    """Short `alternate_names` are the acronyms ("NeurIPS"); the long ones repeat the
    full name. Filter by length and drop anything matching the venue name itself."""
    out: list[str] = []
    for alt in pv.get("alternate_names") or []:
        alt = (alt or "").strip()
        if not alt or len(alt) > 12 or " " in alt.strip():
            continue
        if venue and alt.lower() == venue.lower():
            continue
        if alt.lower() in _NON_VENUES:
            continue
        if alt not in out:
            out.append(alt)
    return out


def _from_s2(rec: dict) -> dict:
    """S2 paper record -> the enrichment fields we store (only non-empty ones)."""
    pv = rec.get("publicationVenue") or {}
    venue = _clean_venue(pv.get("name") or rec.get("venue"))
    out: dict = {}
    if venue:
        out["venue"] = venue
        if pv.get("type"):
            out["venue_type"] = pv["type"]
        acronyms = _acronyms(pv, venue)
        if acronyms:
            out["venue_acronyms"] = acronyms
    if rec.get("citationCount") is not None:
        out["citation_count"] = int(rec["citationCount"])
    if rec.get("influentialCitationCount") is not None:
        out["influential_citation_count"] = int(rec["influentialCitationCount"])
    if out:
        out["enriched_via"] = "semantic_scholar"
    return out


def fetch_s2_batch(s2_ids: list[str]) -> dict[str, dict]:
    """{s2_id: enrichment fields} for the ids S2 knows. Missing ids are simply absent.

    S2 returns results positionally with null for unknown ids, so the response is zipped
    back against the request rather than matched on the echoed id.
    """
    found: dict[str, dict] = {}
    for start in range(0, len(s2_ids), _S2_BATCH_LIMIT):
        chunk = s2_ids[start:start + _S2_BATCH_LIMIT]
        try:
            results = post_json(
                f"{_S2_BATCH}?fields={_S2_FIELDS}",
                {"ids": chunk},
                headers=_api_key_headers(),
                retries=_s2_retries(),
                max_backoff=_S2_MAX_BACKOFF,
            )
        except Exception:
            continue  # best-effort: leave this chunk unenriched
        if not isinstance(results, list) or len(results) != len(chunk):
            continue
        for s2_id, rec in zip(chunk, results):
            if not rec:
                continue
            fields = _from_s2(rec)
            if fields:
                found[s2_id] = fields
    return found


def fetch_openalex(arxiv_id: Optional[str], doi: Optional[str]) -> dict:
    """Citations (and a venue when there is a real one) from OpenAlex; {} on any miss.

    Needs no key. Keyed by an arXiv DOI it returns the *preprint* record, whose venue is
    arXiv itself — so a venue is read only from a record that is not a preprint and whose
    source is not a repository. The citation count is taken either way.
    """
    key = f"doi:{doi}" if doi else (f"doi:10.48550/arXiv.{arxiv_id}" if arxiv_id else None)
    if not key:
        return {}
    url = f"{_OPENALEX_WORK}/{key}"
    if _mailto():
        url += f"?mailto={_mailto()}"
    try:
        rec = get_json(url)
    except Exception:
        return {}
    out: dict = {}
    if rec.get("cited_by_count") is not None:
        out["citation_count"] = int(rec["cited_by_count"])
    source = ((rec.get("primary_location") or {}).get("source")) or {}
    venue = _clean_venue(source.get("display_name"))
    if venue and rec.get("type") != "preprint" and source.get("type") != "repository":
        out["venue"] = venue
        if source.get("type"):
            out["venue_type"] = source["type"]
    if out:
        out["enriched_via"] = "openalex"
    return out


def _apply(metadata: dict, fields: dict) -> bool:
    """Write enrichment fields into a metadata dict. Returns whether anything changed.

    A venue already present (arXiv's `journal_ref`) is not overwritten — it is the
    author's own statement of record — but the enrichment still supplies the acronyms,
    type and counts it could not provide.
    """
    if not fields:
        return False
    changed = False
    for key, value in fields.items():
        if key == "venue" and metadata.get("venue"):
            continue
        if metadata.get(key) != value:
            metadata[key] = value
            changed = True
    if changed:
        metadata["enriched_at"] = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return changed


def enrich_record(record: dict) -> bool:
    """Enrich one paper record in place. Returns whether it changed.

    Never raises: a paper that cannot be enriched is still a usable paper.
    """
    metadata = record.setdefault("metadata", {})
    fields: dict = {}
    s2_id = s2_id_for(metadata)
    if s2_id:
        fields = fetch_s2_batch([s2_id]).get(s2_id, {})
    if fields.get("citation_count") is None or not fields.get("venue"):
        ids = metadata.get("identifiers") or {}
        fallback = fetch_openalex(ids.get("arxiv_id"), ids.get("doi"))
        if fallback:
            # S2's fields win where it had them; OpenAlex fills only the gaps
            fields = {**fallback, **fields}
            if not fields.get("enriched_via"):
                fields["enriched_via"] = "openalex"
    return _apply(metadata, fields)


def enrich_records(records: Iterable[dict]) -> int:
    """Batch-enrich many records in place (one S2 request per 500). Returns how many
    changed. Used by the backfill script; the per-fetch path uses `enrich_record`."""
    records = list(records)
    by_id: dict[str, list[dict]] = {}
    for record in records:
        s2_id = s2_id_for(record.get("metadata") or {})
        if s2_id:
            by_id.setdefault(s2_id, []).append(record)
    found = fetch_s2_batch(sorted(by_id))
    changed = 0
    for s2_id, group in by_id.items():
        for record in group:
            if _apply(record.setdefault("metadata", {}), found.get(s2_id, {})):
                changed += 1
    return changed
