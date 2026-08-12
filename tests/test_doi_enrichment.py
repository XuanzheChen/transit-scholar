"""DOI metadata enrichment tests.

All provider calls are mocked or network-disabled. These tests must never
contact Crossref, OpenAlex, or Semantic Scholar.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import inspect

from transit_scholar import config as _config
from transit_scholar.db.engine import SessionLocal, engine as _engine
from transit_scholar.db.models import (
    DOIEnrichmentJob,
    DOIProviderResult,
    Paper,
    PaperAuthor,
)
from transit_scholar.doi_enrichment.clients.base import ProviderClient
from transit_scholar.doi_enrichment.clients.crossref import CrossrefClient
from transit_scholar.doi_enrichment.clients.openalex import OpenAlexClient, sanitize_url
from transit_scholar.doi_enrichment.clients.semantic_scholar import SemanticScholarClient
from transit_scholar.doi_enrichment.providers import ProviderRegistry
from transit_scholar.doi_enrichment.result import (
    PROVIDER_FETCHED,
    ProviderFetchResult,
)
from transit_scholar.doi_enrichment.service import (
    collect_provider_results,
    enrich_paper_by_doi,
)
from transit_scholar.web.app import enrichment, enrichment_refresh


@pytest.fixture(autouse=True)
def _reset_database():
    with SessionLocal() as session:
        session.query(DOIProviderResult).delete()
        session.query(DOIEnrichmentJob).delete()
        session.query(PaperAuthor).delete()
        session.query(Paper).delete()
        session.commit()
    yield


def _make_paper(*, doi: str | None = "10.1000/example") -> str:
    with SessionLocal() as session:
        paper = Paper(
            title=None,
            normalized_title=None,
            doi=doi,
            normalized_doi=doi,
            status="active",
        )
        session.add(paper)
        session.commit()
        return paper.id


class FakeClient(ProviderClient):
    def __init__(self, name: str, raw_json: str) -> None:
        self.name = name
        self.timeout_seconds = 1
        self.max_attempts = 1
        self.raw_json = raw_json
        self.calls = 0

    def build_request(self, doi: str) -> tuple[str, dict[str, str]]:
        return f"https://example.invalid/{self.name}/{doi}", {"Accept": "application/json"}

    def fetch(self, doi: str) -> ProviderFetchResult:
        self.calls += 1
        return ProviderFetchResult(
            provider=self.name,
            status=PROVIDER_FETCHED,
            http_status=200,
            raw_json=self.raw_json,
        )


def test_doi_enrichment_tables_present_and_papers_unchanged():
    tables = set(inspect(_engine).get_table_names())
    assert "doi_enrichment_jobs" in tables
    assert "doi_provider_results" in tables
    paper_columns = {c["name"] for c in inspect(_engine).get_columns("papers")}
    assert "metadata_enrichment_status" not in paper_columns


def test_env_example_exists_and_contains_expected_fields():
    content = Path(".env.example").read_text(encoding="utf-8")
    for key in (
        "OPENALEX_API_KEY=",
        "SEMANTIC_SCHOLAR_API_KEY=",
        "CROSSREF_MAILTO=",
        "TRANSIT_SCHOLAR_METADATA_ENRICHMENT_ENABLED=true",
        "TRANSIT_SCHOLAR_METADATA_ENRICHMENT_STRICT_DOI=true",
        "TRANSIT_SCHOLAR_METADATA_ENRICHMENT_ALLOW_NETWORK=false",
    ):
        assert key in content


def test_openalex_missing_key_writes_skipped_provider_record():
    paper_id = _make_paper()
    old_key = _config.settings.openalex_api_key
    old_network = _config.settings.metadata_enrichment_allow_network
    _config.settings.openalex_api_key = None
    _config.settings.metadata_enrichment_allow_network = False
    try:
        result = enrich_paper_by_doi(paper_id)
    finally:
        _config.settings.openalex_api_key = old_key
        _config.settings.metadata_enrichment_allow_network = old_network

    assert any(
        p.provider == "openalex"
        and p.status == "skipped"
        and p.error_code == "missing_api_key"
        for p in result.providers
    )
    with SessionLocal() as session:
        record = session.query(DOIProviderResult).filter_by(provider="openalex").one()
        assert record.status == "skipped"
        assert record.error_code == "missing_api_key"


def test_missing_doi_derives_skipped_without_job():
    paper_id = _make_paper(doi=None)
    result = enrich_paper_by_doi(paper_id)
    assert result.status == "skipped"
    assert result.error_code == "stable_identifier_missing:doi"
    collected = collect_provider_results(paper_id)
    assert collected is not None
    assert collected.status == "skipped"
    with SessionLocal() as session:
        assert session.query(DOIEnrichmentJob).count() == 0


def test_provider_metadata_merges_into_empty_paper_without_network():
    paper_id = _make_paper()
    raw = json.dumps({
        "message": {
            "DOI": "10.1000/example",
            "title": ["External DOI Title"],
            "author": [{"given": "Ada", "family": "Lovelace"}],
            "issued": {"date-parts": [[2024]]},
            "container-title": ["Journal of Examples"],
        }
    })
    fake = FakeClient("crossref", raw)
    registry = ProviderRegistry(clients={
        "crossref": fake,
        "semantic_scholar": FakeClient("semantic_scholar", "{}"),
    })
    old_network = _config.settings.metadata_enrichment_allow_network
    old_openalex = _config.settings.openalex_api_key
    _config.settings.metadata_enrichment_allow_network = True
    _config.settings.openalex_api_key = None
    try:
        result = enrich_paper_by_doi(paper_id, registry=registry)
    finally:
        _config.settings.metadata_enrichment_allow_network = old_network
        _config.settings.openalex_api_key = old_openalex

    assert result.status == "fetched"
    assert fake.calls == 1
    with SessionLocal() as session:
        paper = session.get(Paper, paper_id)
        assert paper.title == "External DOI Title"
        assert paper.publication_year == 2024
        assert paper.venue == "Journal of Examples"
        authors = session.query(PaperAuthor).filter_by(paper_id=paper_id).all()
        assert [a.full_name for a in authors] == ["Ada Lovelace"]


def test_cached_provider_results_keep_job_fetched_when_network_disabled():
    paper_id = _make_paper()
    raw = json.dumps({
        "message": {
            "DOI": "10.1000/example",
            "title": ["Cached DOI Title"],
            "author": [{"given": "Ada", "family": "Lovelace"}],
            "issued": {"date-parts": [[2024]]},
        }
    })
    fake = FakeClient("crossref", raw)
    registry = ProviderRegistry(clients={
        "crossref": fake,
        "semantic_scholar": FakeClient("semantic_scholar", "{}"),
    })
    old_network = _config.settings.metadata_enrichment_allow_network
    old_openalex = _config.settings.openalex_api_key
    _config.settings.openalex_api_key = None
    try:
        _config.settings.metadata_enrichment_allow_network = True
        first = enrich_paper_by_doi(paper_id, registry=registry)
        assert first.status == "fetched"

        _config.settings.metadata_enrichment_allow_network = False
        second = enrich_paper_by_doi(paper_id, registry=registry)
    finally:
        _config.settings.metadata_enrichment_allow_network = old_network
        _config.settings.openalex_api_key = old_openalex

    assert second.status == "fetched"
    crossref = [p for p in second.providers if p.provider == "crossref"][0]
    assert crossref.status == "fetched"
    assert crossref.attempt_count == 1


def test_api_key_material_is_not_returned_by_request_builders():
    old_openalex = _config.settings.openalex_api_key
    old_semantic = _config.settings.semantic_scholar_api_key
    _config.settings.openalex_api_key = "OPENALEX_SECRET_VALUE"
    _config.settings.semantic_scholar_api_key = "S2_SECRET_VALUE"
    try:
        openalex_url, _ = OpenAlexClient().build_request("10.1000/example")
        safe_openalex_url = sanitize_url(openalex_url)
        _, semantic_headers = SemanticScholarClient().build_request("10.1000/example")
    finally:
        _config.settings.openalex_api_key = old_openalex
        _config.settings.semantic_scholar_api_key = old_semantic

    assert "OPENALEX_SECRET_VALUE" not in safe_openalex_url
    assert "S2_SECRET_VALUE" not in json.dumps(semantic_headers)


def test_crossref_mailto_request_builder():
    old_mailto = _config.settings.crossref_mailto
    _config.settings.crossref_mailto = "user@example.com"
    try:
        url, headers = CrossrefClient().build_request("10.1000/example")
    finally:
        _config.settings.crossref_mailto = old_mailto

    assert "mailto=user@example.com" in url
    assert "user@example.com" in headers["User-Agent"]


def test_enrichment_routes_return_structure_and_refresh_is_network_gated():
    paper_id = _make_paper(doi=None)
    data = enrichment(paper_id)
    assert data["metadata_enrichment_status"] == "skipped"

    old_network = _config.settings.metadata_enrichment_allow_network
    _config.settings.metadata_enrichment_allow_network = False
    try:
        refresh = enrichment_refresh(paper_id)
    finally:
        _config.settings.metadata_enrichment_allow_network = old_network
    assert refresh["error_code"] == "network_disabled"
