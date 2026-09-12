"""Venue + citation enrichment, and the impact scoring that consumes it."""
import datetime as dt

from src.agents.ranking_agent.schemas import RankingConfig
from src.agents.ranking_agent.tools import score_impact as si
from src.common.schemas.gap import GapRecord
from src.common.schemas.paper import PaperRecord
from src.common.tools.paper_fetch import enrich


# --- enrich.py --------------------------------------------------------------------

_S2_NEURIPS = {
	"venue": "Neural Information Processing Systems",
	"publicationVenue": {
		"name": "Neural Information Processing Systems",
		"type": "conference",
		"alternate_names": ["Neural Inf Process Syst", "NeurIPS", "NIPS"],
	},
	"year": 2023,
	"citationCount": 4767,
	"influentialCitationCount": 305,
}


def test_s2_record_yields_venue_type_and_acronyms():
	fields = enrich._from_s2(_S2_NEURIPS)

	assert fields["venue"] == "Neural Information Processing Systems"
	assert fields["venue_type"] == "conference"
	# long-form alternates are dropped; only the acronyms survive
	assert fields["venue_acronyms"] == ["NeurIPS", "NIPS"]
	assert fields["citation_count"] == 4767
	assert fields["enriched_via"] == "semantic_scholar"


def test_arxiv_is_not_treated_as_a_venue():
	"""A paper that only ever appeared on arXiv has no venue — recording 'arXiv.org'
	as one would imply a peer-reviewed venue it does not have."""
	fields = enrich._from_s2({
		"venue": "arXiv.org",
		"publicationVenue": {"name": "arXiv.org", "alternate_names": ["ArXiv"]},
		"citationCount": 5,
	})

	assert "venue" not in fields
	assert "venue_acronyms" not in fields
	assert fields["citation_count"] == 5  # the count is still real signal


def test_existing_venue_is_not_overwritten_but_is_augmented():
	"""arXiv's journal_ref is the author's own statement of record; enrichment fills in
	only what that string cannot carry."""
	metadata = {"venue": "In NeurIPS 2023 proceedings"}

	assert enrich._apply(metadata, enrich._from_s2(_S2_NEURIPS)) is True
	assert metadata["venue"] == "In NeurIPS 2023 proceedings"
	assert metadata["venue_acronyms"] == ["NeurIPS", "NIPS"]
	assert metadata["citation_count"] == 4767
	assert metadata["enriched_at"].endswith("Z")


def test_s2_lookup_key_prefers_arxiv_then_doi():
	assert enrich.s2_id_for({"identifiers": {"arxiv_id": "2305.10601", "doi": "10.1/x"}}) == "ARXIV:2305.10601"
	assert enrich.s2_id_for({"identifiers": {"doi": "10.1/x"}}) == "DOI:10.1/x"
	assert enrich.s2_id_for({"identifiers": {}}) is None


def test_batch_skips_unknown_ids_without_shifting_the_rest(monkeypatch):
	"""S2 returns results positionally with null for ids it does not know, so a miss in
	the middle must not slide the following records onto the wrong papers."""
	monkeypatch.setattr(enrich, "post_json", lambda url, payload, **kw: [
		_S2_NEURIPS, None, {"venue": "arXiv.org", "citationCount": 5},
	])

	found = enrich.fetch_s2_batch(["ARXIV:a", "ARXIV:b", "ARXIV:c"])

	assert found["ARXIV:a"]["venue_acronyms"] == ["NeurIPS", "NIPS"]
	assert "ARXIV:b" not in found
	assert found["ARXIV:c"]["citation_count"] == 5


def test_batch_of_wrong_length_is_discarded(monkeypatch):
	"""Without a positional guarantee the mapping is meaningless — better unenriched."""
	monkeypatch.setattr(enrich, "post_json", lambda url, payload, **kw: [_S2_NEURIPS])

	assert enrich.fetch_s2_batch(["ARXIV:a", "ARXIV:b"]) == {}


def test_provider_failure_leaves_the_record_untouched(monkeypatch):
	def boom(*a, **kw):
		raise RuntimeError("429")

	monkeypatch.setattr(enrich, "post_json", boom)
	monkeypatch.setattr(enrich, "get_json", boom)
	record = {"metadata": {"identifiers": {"arxiv_id": "2305.10601"}}}

	assert enrich.enrich_record(record) is False
	assert record["metadata"] == {"identifiers": {"arxiv_id": "2305.10601"}}


def test_openalex_fills_citations_when_s2_has_no_record(monkeypatch):
	monkeypatch.setattr(enrich, "post_json", lambda url, payload, **kw: [None])
	monkeypatch.setattr(enrich, "get_json", lambda url, **kw: {"cited_by_count": 573})
	record = {"metadata": {"identifiers": {"arxiv_id": "2305.10601"}}}

	assert enrich.enrich_record(record) is True
	assert record["metadata"]["citation_count"] == 573
	assert record["metadata"]["enriched_via"] == "openalex"


def test_enriched_fields_round_trip_through_the_schema():
	record = PaperRecord.model_validate({
		"paper_id": "arxiv_2305.10601",
		"metadata": {"year": 2023, **enrich._from_s2(_S2_NEURIPS)},
	})

	assert record.metadata.venue_acronyms == ["NeurIPS", "NIPS"]
	assert record.metadata.venue_type == "conference"
	assert record.metadata.citation_count == 4767


def test_retry_budget_widens_without_an_api_key(monkeypatch):
	"""Keyless S2 rejects ~2 of 3 attempts but rejects them cheaply, so the keyless path
	must buy many more attempts than the keyed one."""
	monkeypatch.delenv("SEMANTIC_SCHOLAR_API_KEY", raising=False)
	assert enrich._s2_retries() == enrich._S2_RETRIES_KEYLESS
	assert enrich._api_key_headers() == {}

	monkeypatch.setenv("SEMANTIC_SCHOLAR_API_KEY", "k")
	assert enrich._s2_retries() == enrich._S2_RETRIES_KEYED
	assert enrich._api_key_headers() == {"x-api-key": "k"}
	assert enrich._S2_RETRIES_KEYLESS > enrich._S2_RETRIES_KEYED


def test_keyless_batch_passes_its_retry_budget_through(monkeypatch):
	seen = {}

	def capture(url, payload, **kw):
		seen.update(kw)
		return [_S2_NEURIPS]

	monkeypatch.delenv("SEMANTIC_SCHOLAR_API_KEY", raising=False)
	monkeypatch.setattr(enrich, "post_json", capture)
	enrich.fetch_s2_batch(["ARXIV:a"])

	assert seen["retries"] == enrich._S2_RETRIES_KEYLESS
	assert seen["max_backoff"] == enrich._S2_MAX_BACKOFF
	assert seen["headers"] == {}  # keyless request carries no auth header


def test_openalex_refuses_a_preprint_venue_but_keeps_the_count(monkeypatch):
	"""The arXiv DOI resolves to the preprint record; 'arXiv (Cornell University)' is not
	a venue the paper was published at."""
	monkeypatch.setattr(enrich, "get_json", lambda url, **kw: {
		"type": "preprint",
		"cited_by_count": 573,
		"primary_location": {"source": {"display_name": "arXiv (Cornell University)",
		                                "type": "repository"}},
	})

	fields = enrich.fetch_openalex("2305.10601", None)

	assert fields["citation_count"] == 573
	assert "venue" not in fields


def test_openalex_supplies_a_venue_for_a_published_record(monkeypatch):
	monkeypatch.setattr(enrich, "get_json", lambda url, **kw: {
		"type": "article",
		"cited_by_count": 42,
		"primary_location": {"source": {"display_name": "Nature", "type": "journal"}},
	})

	fields = enrich.fetch_openalex(None, "10.1/x")

	assert fields["venue"] == "Nature"
	assert fields["venue_type"] == "journal"


def test_openalex_fills_only_what_s2_left_missing(monkeypatch):
	"""S2 had the venue but no count; OpenAlex must not clobber the better venue data."""
	monkeypatch.setattr(enrich, "post_json", lambda url, payload, **kw: [
		{"publicationVenue": {"name": "Neural Information Processing Systems",
		                      "type": "conference", "alternate_names": ["NeurIPS"]}},
	])
	monkeypatch.setattr(enrich, "get_json", lambda url, **kw: {
		"type": "preprint", "cited_by_count": 573,
		"primary_location": {"source": {"display_name": "arXiv", "type": "repository"}},
	})
	record = {"metadata": {"identifiers": {"arxiv_id": "2305.10601"}}}

	enrich.enrich_record(record)

	assert record["metadata"]["venue"] == "Neural Information Processing Systems"
	assert record["metadata"]["venue_acronyms"] == ["NeurIPS"]
	assert record["metadata"]["citation_count"] == 573
	assert record["metadata"]["enriched_via"] == "semantic_scholar"


# --- score_impact.py --------------------------------------------------------------

def _gap(paper_ids):
	return GapRecord.model_validate({
		"gap_id": "gap_test",
		"title": "t",
		"description": "d",
		"type": "negative_result",
		"canonical_description": "cd",
		"extracted_at": "2026-01-01T00:00:00Z",
		"evidence": [
			{"paper_id": pid, "section": "s", "anchor": "a", "quote": "q"}
			for pid in paper_ids
		],
	})


def _stub_papers(monkeypatch, papers):
	"""papers: {paper_id: {year, venue, venue_type, acronyms, citations}}"""
	monkeypatch.setattr(si, "get_year", lambda c, p: papers[p].get("year"))
	monkeypatch.setattr(si, "get_venue", lambda c, p: papers[p].get("venue"))
	monkeypatch.setattr(si, "get_venue_type", lambda c, p: papers[p].get("venue_type"))
	monkeypatch.setattr(si, "get_venue_acronyms", lambda c, p: papers[p].get("acronyms", []))
	monkeypatch.setattr(si, "get_citation_count", lambda c, p: papers[p].get("citations"))
	monkeypatch.setattr(si, "resolve_venues", lambda names: {n: n for n in names})


def test_evidence_bonus_counts_distinct_papers_not_quotes():
	"""Two quotes from one paper is one corroborating source."""
	config = RankingConfig()

	assert si._evidence_bonus(_gap(["p1", "p1"]), config) == 1.0
	assert si._evidence_bonus(_gap(["p1", "p2"]), config) > 1.0


def test_provider_acronym_is_used_without_the_llm_resolver(monkeypatch):
	_stub_papers(monkeypatch, {"p1": {"year": 2023, "venue": "Neural Information Processing Systems",
	                                  "venue_type": "conference", "acronyms": ["NeurIPS"]}})
	# a resolver call here would mean the provider acronym was ignored
	monkeypatch.setattr(si, "resolve_venues", lambda names: {} if not names else pytest_fail(names))

	breakdown = si.compute_impact(_gap(["p1"]), "cat", RankingConfig())

	assert breakdown.venues == ["NeurIPS"]
	assert breakdown.venue_score == 1.0


def pytest_fail(names):
	raise AssertionError(f"LLM resolver called for {names}, provider acronym should have won")


def test_workshop_venue_does_not_inherit_its_host_conference_rank(monkeypatch):
	"""'4th Deployable AI Workshop at AAAI 2026' resolves to AAAI by name; scoring it
	would award a workshop paper the flagship conference's A*."""
	_stub_papers(monkeypatch, {"p1": {
		"year": 2026, "venue": "4th Deployable AI Workshop at AAAI 2026", "venue_type": "workshop",
	}})
	monkeypatch.setattr(si, "resolve_venues", lambda names: {n: "AAAI" for n in names})

	breakdown = si.compute_impact(_gap(["p1"]), "cat", RankingConfig())

	assert breakdown.venue_score is None
	assert breakdown.venues == []


def test_citation_score_reads_a_rate_not_a_total():
	"""A 2015 paper must not outscore a 2024 one merely for having had longer to
	accumulate citations."""
	config = RankingConfig()
	this_year = dt.date.today().year
	old = si._citation_score(500, this_year - 10, config)
	new = si._citation_score(500, this_year - 1, config)

	assert new > old


def test_zero_citations_is_a_signal_but_missing_is_not():
	config = RankingConfig()

	assert si._citation_score(0, 2023, config) == 0.0
	assert si._citation_score(None, 2023, config) is None


def test_citations_differentiate_gaps_that_were_previously_identical(monkeypatch):
	"""The original defect: with no venue and no citation source every gap scored the
	same neutral 0.5 quality."""
	_stub_papers(monkeypatch, {
		"strong": {"year": 2023, "citations": 4767},
		"weak": {"year": 2023, "citations": 5},
	})
	config = RankingConfig()

	strong = si.compute_impact(_gap(["strong"]), "cat", config)
	weak = si.compute_impact(_gap(["weak"]), "cat", config)

	assert strong.score > weak.score
	assert strong.quality != 0.5 and weak.quality != 0.5


def test_unenriched_papers_still_fall_back_to_the_neutral_prior(monkeypatch):
	_stub_papers(monkeypatch, {"p1": {"year": 2023}})

	breakdown = si.compute_impact(_gap(["p1"]), "cat", RankingConfig())

	assert breakdown.quality == 0.5
	assert breakdown.venue_score is None and breakdown.citation is None


def test_best_paper_wins_on_both_signals(monkeypatch):
	"""A weak companion paper must not drag down a gap raised by a strong one."""
	_stub_papers(monkeypatch, {
		"strong": {"year": 2023, "citations": 4767, "acronyms": ["NeurIPS"], "venue_type": "conference"},
		"weak": {"year": 2023, "citations": 1},
	})

	breakdown = si.compute_impact(_gap(["strong", "weak"]), "cat", RankingConfig())

	assert breakdown.venue_score == 1.0
	assert breakdown.citation == si._citation_score(4767, 2023, RankingConfig())
