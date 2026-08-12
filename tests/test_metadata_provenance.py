"""Metadata candidate provenance and deterministic selection tests.

Covers AC-PROV-001..007 and AC-DOI-001/002: provider scalar persistence with
exact provider source_location, ordered author round-trip through the frozen
canonical JSON aggregate, idempotent refresh, at-most-one selected candidate,
provider-over-heuristic replacement, manual_confirmed survival across session
close/reopen and forced refresh, no-confidence-threshold selection with
deterministic ties, DOI variant normalization on both sides, and DOI mismatch
facts with zero contributed candidates. All provider calls are mocked; no
network access.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import inspect, select

from transit_scholar import config as _config
from transit_scholar.db.engine import SessionLocal, engine as _engine
from transit_scholar.db.models import (
    AuditLog,
    CitationRecord,
    CitationRender,
    DOIEnrichmentJob,
    DOIProviderResult,
    IngestionJob,
    MetadataCandidate,
    Paper,
    PaperAuthor,
    PaperFile,
    PaperRelation,
)
from transit_scholar.doi_enrichment.clients.base import ProviderClient
from transit_scholar.doi_enrichment.providers import ProviderRegistry
from transit_scholar.doi_enrichment.result import (
    PROVIDER_FETCHED,
    ProviderFetchResult,
)
from transit_scholar.doi_enrichment.service import enrich_paper_by_doi
from transit_scholar.identity.service import (
    INVALID_FIELDS,
    update_paper_metadata,
)
from transit_scholar.metadata.normalizers import (
    normalize_arxiv_id,
    normalize_doi,
    normalize_title,
)
from transit_scholar.metadata.selection import (
    MANUAL_SOURCE_LOCATION,
    MANUAL_SOURCE_TYPE,
    PROVIDER_SOURCE_TYPE,
    NoPrimaryFileError,
    get_primary_file,
    reselect_and_materialize,
    select_candidates,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_database():
    """Clear all business tables before each test for isolation."""
    with SessionLocal() as session:
        session.query(MetadataCandidate).delete()
        session.query(CitationRender).delete()
        session.query(CitationRecord).delete()
        session.query(IngestionJob).delete()
        session.query(PaperRelation).delete()
        session.query(AuditLog).delete()
        session.query(PaperAuthor).delete()
        session.query(DOIProviderResult).delete()
        session.query(DOIEnrichmentJob).delete()
        session.query(PaperFile).delete()
        session.query(Paper).delete()
        session.commit()
    yield


class FakeClient(ProviderClient):
    """A provider client that returns canned JSON without network access."""

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


def _make_paper_with_primary_file(*, doi: str = "10.1000/example") -> tuple[str, str]:
    """Create a paper with one non-deleted primary file. Returns (paper_id, file_id)."""
    with SessionLocal() as session:
        paper = Paper(
            doi=doi,
            normalized_doi=doi,
            status="active",
        )
        session.add(paper)
        session.flush()
        pf = PaperFile(
            paper_id=paper.id,
            is_primary=True,
            relative_path=f"library/originals/{paper.id}/source.pdf",
        )
        session.add(pf)
        session.commit()
        return paper.id, pf.id


def _make_fileless_paper(*, doi: str = "10.1000/example") -> str:
    with SessionLocal() as session:
        paper = Paper(doi=doi, normalized_doi=doi, status="active")
        session.add(paper)
        session.commit()
        return paper.id


def _add_candidate(
    paper_id: str,
    file_id: str,
    field_name: str,
    value_text: str,
    source_type: str,
    source_location: str,
    confidence: float,
) -> None:
    with SessionLocal() as session:
        session.add(MetadataCandidate(
            paper_id=paper_id,
            paper_file_id=file_id,
            field_name=field_name,
            value_text=value_text,
            source_type=source_type,
            source_location=source_location,
            confidence=confidence,
        ))
        session.commit()


def _add_heuristic_state(paper_id: str, file_id: str) -> None:
    """Simulate the extraction sync state: heuristic candidates + materialized
    Paper/PaperAuthor values (legacy field_name=author candidates included)."""
    with SessionLocal() as session:
        paper = session.get(Paper, paper_id)
        paper.title = "Heuristic Title"
        paper.normalized_title = "heuristic title"
        paper.publication_year = 1999
        paper.venue = "Heuristic Venue"
        paper.abstract = "Heuristic abstract body long enough to be usable."
        session.add(PaperAuthor(
            paper_id=paper_id, author_order=1, full_name="Bob Smith",
            normalized_name="bob smith",
        ))
        session.add(PaperAuthor(
            paper_id=paper_id, author_order=2, full_name="Carol Jones",
            normalized_name="carol jones",
        ))
        session.flush()
        session.add(MetadataCandidate(
            paper_id=paper_id, paper_file_id=file_id, field_name="title",
            value_text="Heuristic Title", source_type="pdf_metadata",
            source_location="pdf_metadata", confidence=0.8,
        ))
        session.add(MetadataCandidate(
            paper_id=paper_id, paper_file_id=file_id, field_name="publication_year",
            value_text="1999", source_type="pdf_metadata",
            source_location="pdf_metadata", confidence=0.7,
        ))
        session.add(MetadataCandidate(
            paper_id=paper_id, paper_file_id=file_id, field_name="venue",
            value_text="Heuristic Venue", source_type="pdf_metadata",
            source_location="pdf_metadata", confidence=0.6,
        ))
        session.add(MetadataCandidate(
            paper_id=paper_id, paper_file_id=file_id, field_name="abstract",
            value_text="Heuristic abstract body long enough to be usable.",
            source_type="pdf_metadata", source_location="pdf_metadata", confidence=0.5,
        ))
        session.add(MetadataCandidate(
            paper_id=paper_id, paper_file_id=file_id, field_name="author",
            value_text="Bob Smith", source_type="pdf_metadata",
            source_location="pdf_metadata", confidence=0.8, is_selected=True,
        ))
        session.add(MetadataCandidate(
            paper_id=paper_id, paper_file_id=file_id, field_name="author",
            value_text="Carol Jones", source_type="pdf_metadata",
            source_location="pdf_metadata", confidence=0.8, is_selected=True,
        ))
        session.commit()


def _enrich(
    paper_id: str,
    clients: dict[str, FakeClient],
    *,
    force: bool = False,
    openalex_key: str | None = None,
):
    """Run enrichment with mocked clients and network enabled.

    ProviderRegistry.ordered_clients() indexes every cascade provider, so the
    registry is completed with harmless empty clients for providers the test
    does not exercise.
    """
    registry_clients = dict(clients)
    registry_clients.setdefault("openalex", FakeClient("openalex", "{}"))
    registry_clients.setdefault("semantic_scholar", FakeClient("semantic_scholar", "{}"))
    old_network = _config.settings.metadata_enrichment_allow_network
    old_key = _config.settings.openalex_api_key
    _config.settings.metadata_enrichment_allow_network = True
    _config.settings.openalex_api_key = openalex_key
    try:
        return enrich_paper_by_doi(
            paper_id,
            registry=ProviderRegistry(clients=registry_clients),
            force=force,
        )
    finally:
        _config.settings.metadata_enrichment_allow_network = old_network
        _config.settings.openalex_api_key = old_key


def _crossref_json(
    *,
    doi: str = "10.1000/example",
    title: str | None = None,
    authors: list[dict] | None = None,
    year: int | None = None,
    venue: str | None = None,
    publisher: str | None = None,
    abstract: str | None = None,
) -> str:
    message: dict = {"DOI": doi}
    if title is not None:
        message["title"] = [title]
    if authors is not None:
        message["author"] = authors
    if year is not None:
        message["issued"] = {"date-parts": [[year]]}
    if venue is not None:
        message["container-title"] = [venue]
    if publisher is not None:
        message["publisher"] = publisher
    if abstract is not None:
        message["abstract"] = abstract
    return json.dumps({"message": message})


def _openalex_json(
    *,
    doi: str = "10.1000/example",
    title: str | None = None,
    arxiv_id: str | None = None,
    venue: str | None = None,
    year: int | None = None,
) -> str:
    work: dict = {"doi": doi}
    if title is not None:
        work["title"] = title
    if arxiv_id is not None:
        work["external_ids"] = [{"type": "arxiv", "value": arxiv_id}]
    if venue is not None:
        work["primary_location"] = {"source": {"display_name": venue}}
    if year is not None:
        work["publication_year"] = year
    return json.dumps(work)


def _semantic_json(
    *,
    doi: str = "10.1000/example",
    title: str | None = None,
    authors: list[str] | None = None,
    abstract: str | None = None,
    year: int | None = None,
) -> str:
    paper: dict = {"doi": doi}
    if title is not None:
        paper["title"] = title
    if authors is not None:
        paper["authors"] = [{"name": n} for n in authors]
    if abstract is not None:
        paper["abstract"] = abstract
    if year is not None:
        paper["year"] = year
    return json.dumps(paper)


def _candidates(paper_id: str) -> list[MetadataCandidate]:
    with SessionLocal() as session:
        return session.execute(
            select(MetadataCandidate).where(MetadataCandidate.paper_id == paper_id)
        ).scalars().all()


def _authors(paper_id: str) -> list[str]:
    with SessionLocal() as session:
        rows = session.execute(
            select(PaperAuthor)
            .where(PaperAuthor.paper_id == paper_id)
            .order_by(PaperAuthor.author_order)
        ).scalars().all()
        return [a.full_name for a in rows]


def _author_orders(paper_id: str) -> list[int]:
    with SessionLocal() as session:
        rows = session.execute(
            select(PaperAuthor)
            .where(PaperAuthor.paper_id == paper_id)
            .order_by(PaperAuthor.author_order)
        ).scalars().all()
        return [a.author_order for a in rows]


# ---------------------------------------------------------------------------
# AC-PROV-001 / AC-PROV-004 / AC-PROV-007: three providers, persistence,
# priority selection, one selected candidate
# ---------------------------------------------------------------------------


def test_three_providers_persist_scalars_and_priority_selection():
    paper_id, _ = _make_paper_with_primary_file()
    clients = {
        "crossref": FakeClient(
            "crossref",
            _crossref_json(
                title="Crossref Title",
                year=2024,
                venue="Crossref Journal",
                publisher="Crossref Press",
            ),
        ),
        "openalex": FakeClient(
            "openalex",
            _openalex_json(
                title="Openalex Title",
                arxiv_id="2301.01234v2",
                venue="Openalex Journal",
            ),
        ),
        "semantic_scholar": FakeClient(
            "semantic_scholar",
            _semantic_json(
                title="Semantic Title",
                authors=["Grace Hopper", "Alan Turing"],
                abstract="Semantic abstract body long enough to be usable.",
            ),
        ),
    }
    result = _enrich(paper_id, clients, openalex_key="test-key")

    assert result.status == "fetched"
    with SessionLocal() as session:
        paper = session.get(Paper, paper_id)
        # Materialized from the frozen per-field provider priority.
        assert paper.title == "Crossref Title"
        assert paper.normalized_title == normalize_title("Crossref Title")
        assert paper.publication_year == 2024
        assert paper.venue == "Crossref Journal"
        assert paper.arxiv_id == "2301.01234v2"
        assert paper.abstract == "Semantic abstract body long enough to be usable."
        assert result.resolved["title"] == "doi_provider:crossref"
        assert result.resolved["publication_year"] == "doi_provider:crossref"
        assert result.resolved["venue"] == "doi_provider:crossref"
        assert result.resolved["arxiv_id"] == "doi_provider:openalex"
        assert result.resolved["abstract"] == "doi_provider:semantic_scholar"
        assert result.resolved["authors"] == "doi_provider:semantic_scholar"

        # AC-PROV-001: every usable scalar persisted with the exact provider key.
        rows = _candidates(paper_id)
        by_provider: dict[str, dict[str, str]] = {}
        for c in rows:
            assert c.source_type == PROVIDER_SOURCE_TYPE
            assert c.source_location in ("crossref", "openalex", "semantic_scholar")
            by_provider.setdefault(c.source_location, {})[c.field_name] = c.value_text
        assert by_provider["crossref"]["title"] == "Crossref Title"
        assert by_provider["crossref"]["doi"] == "10.1000/example"
        assert by_provider["crossref"]["publication_year"] == "2024"
        assert by_provider["crossref"]["venue"] == "Crossref Journal"
        assert by_provider["crossref"]["publisher"] == "Crossref Press"
        assert by_provider["openalex"]["title"] == "Openalex Title"
        assert by_provider["openalex"]["doi"] == "10.1000/example"
        assert by_provider["openalex"]["arxiv_id"] == "2301.01234v2"
        assert by_provider["openalex"]["venue"] == "Openalex Journal"
        assert by_provider["semantic_scholar"]["title"] == "Semantic Title"
        assert by_provider["semantic_scholar"]["abstract"] == (
            "Semantic abstract body long enough to be usable."
        )
        # AC-PROV-002: authors round-trip through the canonical JSON array.
        assert by_provider["semantic_scholar"]["authors"] == (
            '["Grace Hopper", "Alan Turing"]'
        )

        # AC-PROV-004: at most one selected candidate per logical field.
        selected_by_field: dict[str, int] = {}
        for c in rows:
            if c.is_selected:
                selected_by_field[c.field_name] = selected_by_field.get(c.field_name, 0) + 1
        for field, count in selected_by_field.items():
            assert count == 1, f"field {field} has {count} selected candidates"
        assert selected_by_field["title"] == 1
        assert selected_by_field["authors"] == 1
        assert selected_by_field["publisher"] == 1

        # AC-PROV-007: priority beats confidence within the provider tier --
        # the unselected openalex/semantic titles still persist as candidates.
        assert by_provider["openalex"]["title"] == "Openalex Title"

        # Ordered authors materialized with 1-based author_order.
        assert _authors(paper_id) == ["Grace Hopper", "Alan Turing"]
        assert _author_orders(paper_id) == [1, 2]

    # Publisher is stored as a candidate but never materialized.
    assert "publisher" not in {c["name"] for c in inspect(_engine).get_columns("papers")}


# ---------------------------------------------------------------------------
# AC-PROV-003: idempotency of repeated (forced) refresh
# ---------------------------------------------------------------------------


def test_repeated_forced_refresh_is_idempotent():
    paper_id, _ = _make_paper_with_primary_file()
    clients = {
        "crossref": FakeClient(
            "crossref",
            _crossref_json(title="Stable Title", year=2024, venue="Stable Venue"),
        ),
    }
    _enrich(paper_id, clients)
    first_candidates = len(_candidates(paper_id))
    first_authors = _authors(paper_id)

    _enrich(paper_id, clients, force=True)
    assert len(_candidates(paper_id)) == first_candidates
    assert _authors(paper_id) == first_authors

    _enrich(paper_id, clients, force=True)
    assert len(_candidates(paper_id)) == first_candidates
    assert _authors(paper_id) == first_authors
    with SessionLocal() as session:
        assert session.query(AuditLog).count() == 0  # no audit noise
        paper = session.get(Paper, paper_id)
        assert paper.title == "Stable Title"
        assert paper.publication_year == 2024
        assert paper.venue == "Stable Venue"


# ---------------------------------------------------------------------------
# AC-PROV-005: provider replaces selected heuristic values
# ---------------------------------------------------------------------------


def test_provider_replaces_selected_heuristic_values():
    paper_id, file_id = _make_paper_with_primary_file()
    _add_heuristic_state(paper_id, file_id)

    clients = {
        "crossref": FakeClient(
            "crossref",
            _crossref_json(
                title="Provider Title",
                year=2024,
                venue="Provider Venue",
                abstract="Provider abstract body long enough to be usable.",
                authors=[{"given": "Ada", "family": "Lovelace"}],
            ),
        ),
    }
    _enrich(paper_id, clients)

    with SessionLocal() as session:
        paper = session.get(Paper, paper_id)
        assert paper.title == "Provider Title"
        assert paper.normalized_title == normalize_title("Provider Title")
        assert paper.publication_year == 2024
        assert paper.venue == "Provider Venue"
        assert paper.abstract == "Provider abstract body long enough to be usable."
        # Heuristic authors were replaced by the provider's ordered list.
        assert _authors(paper_id) == ["Ada Lovelace"]
        assert _author_orders(paper_id) == [1]

        # Legacy heuristic candidates stay readable history, unselected.
        rows = _candidates(paper_id)
        heuristic_title = [
            c for c in rows
            if c.field_name == "title" and c.source_type == "pdf_metadata"
        ]
        assert len(heuristic_title) == 1
        assert heuristic_title[0].value_text == "Heuristic Title"
        assert heuristic_title[0].is_selected is False
        legacy_author = [
            c for c in rows if c.field_name == "author" and c.source_type == "pdf_metadata"
        ]
        assert {c.value_text for c in legacy_author} == {"Bob Smith", "Carol Jones"}
        provider_title = [
            c for c in rows
            if c.field_name == "title" and c.source_type == PROVIDER_SOURCE_TYPE
        ]
        assert len(provider_title) == 1
        assert provider_title[0].is_selected is True


# ---------------------------------------------------------------------------
# AC-PROV-006: manual_confirmed survives session close/reopen + forced refresh
# ---------------------------------------------------------------------------


def test_manual_confirmed_survives_restart_and_forced_refresh():
    paper_id, _ = _make_paper_with_primary_file()
    clients = {
        "crossref": FakeClient(
            "crossref",
            _crossref_json(
                title="Provider Title",
                year=2024,
                authors=[{"given": "Ada", "family": "Lovelace"}],
            ),
        ),
    }
    _enrich(paper_id, clients)
    with SessionLocal() as session:
        assert session.get(Paper, paper_id).title == "Provider Title"

    result = update_paper_metadata(paper_id, {"title": "User Confirmed Title"})
    assert result.status == "updated"
    with SessionLocal() as session:
        assert session.get(Paper, paper_id).title == "User Confirmed Title"
        manual = session.execute(
            select(MetadataCandidate).where(
                MetadataCandidate.paper_id == paper_id,
                MetadataCandidate.field_name == "title",
                MetadataCandidate.source_type == MANUAL_SOURCE_TYPE,
            )
        ).scalars().all()
        assert len(manual) == 1
        assert manual[0].source_location == MANUAL_SOURCE_LOCATION
        assert manual[0].value_text == "User Confirmed Title"

    # "Restart": every read below happens through a fresh session.
    with SessionLocal() as session:
        assert session.get(Paper, paper_id).title == "User Confirmed Title"

    # Forced provider refresh must not override the manual value.
    _enrich(paper_id, clients, force=True)
    with SessionLocal() as session:
        paper = session.get(Paper, paper_id)
        assert paper.title == "User Confirmed Title"
        assert paper.normalized_title == normalize_title("User Confirmed Title")
        selected = [
            c for c in _candidates(paper_id)
            if c.field_name == "title" and c.is_selected
        ]
        assert len(selected) == 1
        assert selected[0].source_type == MANUAL_SOURCE_TYPE
        assert selected[0].source_location == MANUAL_SOURCE_LOCATION

    # Manual authors also survive a forced refresh.
    update_paper_metadata(paper_id, {"authors": ["Manual Author One", "Manual Author Two"]})
    _enrich(paper_id, clients, force=True)
    assert _authors(paper_id) == ["Manual Author One", "Manual Author Two"]
    assert _author_orders(paper_id) == [1, 2]
    with SessionLocal() as session:
        selected_authors = [
            c for c in _candidates(paper_id)
            if c.field_name == "authors" and c.is_selected
        ]
        assert len(selected_authors) == 1
        assert selected_authors[0].source_type == MANUAL_SOURCE_TYPE
        assert json.loads(selected_authors[0].value_text) == [
            "Manual Author One", "Manual Author Two",
        ]


# ---------------------------------------------------------------------------
# AC-PROV-007: no confidence eligibility threshold; deterministic ties
# ---------------------------------------------------------------------------


def test_selection_never_blocked_by_low_confidence():
    paper_id, file_id = _make_paper_with_primary_file()
    _add_candidate(
        paper_id, file_id, "title", "Low Confidence Title",
        "pdf_metadata", "pdf_metadata", 0.05,
    )
    with SessionLocal() as session:
        paper = session.get(Paper, paper_id)
        reselect_and_materialize(session, paper)
        session.commit()
        assert paper.title == "Low Confidence Title"


def test_deterministic_tie_break_and_single_selected():
    paper_id, file_id = _make_paper_with_primary_file()
    _add_candidate(
        paper_id, file_id, "title", "Tie Title A",
        "pdf_metadata", "pdf_metadata", 0.7,
    )
    _add_candidate(
        paper_id, file_id, "title", "Tie Title B",
        "pdf_metadata", "pdf_metadata", 0.7,
    )

    def selected_title() -> str:
        with SessionLocal() as session:
            selected = select_candidates(session, paper_id)
            session.commit()
            return selected["title"].value_text

    first = selected_title()
    second = selected_title()
    assert first == second  # stable across repeated selection
    with SessionLocal() as session:
        rows = session.execute(
            select(MetadataCandidate).where(
                MetadataCandidate.paper_id == paper_id,
                MetadataCandidate.field_name == "title",
            )
        ).scalars().all()
        chosen = [c for c in rows if c.is_selected]
        assert len(chosen) == 1
        assert chosen[0].value_text == first


# ---------------------------------------------------------------------------
# AC-DOI-001: DOI variants normalize identically on both sides
# ---------------------------------------------------------------------------


def test_doi_variants_normalize_identically():
    assert normalize_doi("10.1234/ABC") == "10.1234/abc"
    assert normalize_doi("doi:10.1234/abc") == "10.1234/abc"
    assert normalize_doi("https://doi.org/10.1234/abc") == "10.1234/abc"
    assert normalize_doi("  https://doi.org/10.1234/ABC.  ") == "10.1234/abc"
    variants = [
        "10.1234/abc",
        "doi:10.1234/abc",
        "https://doi.org/10.1234/abc",
        "DOI:10.1234/ABC",
        "  10.1234/abc.  ",
    ]
    assert len({normalize_doi(v) for v in variants}) == 1


def test_provider_doi_url_variant_matches_local_doi():
    # Local DOI is bare; provider returns a doi.org URL with case differences.
    paper_id, _ = _make_paper_with_primary_file(doi="10.1000/example")
    clients = {
        "crossref": FakeClient(
            "crossref",
            _crossref_json(doi="https://doi.org/10.1000/EXAMPLE", title="Variant Title"),
        ),
    }
    result = _enrich(paper_id, clients)
    # The title was adopted, but this provider response carries no authors or
    # year, so the job is partial rather than fetched.
    assert result.status == "partial"
    assert result.resolved["title"] == "doi_provider:crossref"
    with SessionLocal() as session:
        paper = session.get(Paper, paper_id)
        assert paper.title == "Variant Title"
        assert paper.normalized_title == normalize_title("Variant Title")
        crossref = session.execute(
            select(DOIProviderResult).where(
                DOIProviderResult.paper_id == paper_id,
                DOIProviderResult.provider == "crossref",
            )
        ).scalars().first()
        assert crossref.error_code is None
        assert any(
            c.field_name == "title" and c.source_location == "crossref"
            for c in _candidates(paper_id)
        )


def test_selected_provider_doi_and_arxiv_materialize_into_paper():
    """FR-PROV-007: every selected provider DOI/arXiv candidate materializes,
    so Paper state matches the selected candidate exactly (no fill-only
    exception for identity fields)."""
    paper_id, _ = _make_paper_with_primary_file(doi="10.1000/example")
    with SessionLocal() as session:
        paper = session.get(Paper, paper_id)
        paper.arxiv_id = "2301.01234v1"
        session.commit()

    clients = {
        "crossref": FakeClient(
            "crossref",
            _crossref_json(
                doi="https://doi.org/10.1000/EXAMPLE",
                title="Identity Materialization Title",
                year=2024,
                authors=[{"given": "Ada", "family": "Lovelace"}],
            ),
        ),
        "openalex": FakeClient("openalex", _openalex_json(arxiv_id="2301.01234v2")),
    }
    result = _enrich(paper_id, clients, openalex_key="test-key")
    assert result.status == "fetched"

    with SessionLocal() as session:
        paper = session.get(Paper, paper_id)
        selected = {c.field_name: c for c in _candidates(paper_id) if c.is_selected}
        doi_c = selected["doi"]
        arxiv_c = selected["arxiv_id"]
        assert doi_c.source_type == PROVIDER_SOURCE_TYPE
        assert arxiv_c.source_type == PROVIDER_SOURCE_TYPE
        # Paper must match the selected candidates exactly.
        assert paper.doi == doi_c.value_text.strip()
        assert paper.normalized_doi == normalize_doi(doi_c.value_text)
        assert paper.normalized_doi == "10.1000/example"
        assert paper.arxiv_id == normalize_arxiv_id(arxiv_c.value_text)
        assert paper.arxiv_id == "2301.01234v2"


# ---------------------------------------------------------------------------
# AC-DOI-002: DOI mismatch persists a fact and contributes zero candidates
# ---------------------------------------------------------------------------


def test_doi_mismatch_persists_fact_and_contributes_no_candidates():
    paper_id, _ = _make_paper_with_primary_file(doi="10.1000/example")
    clients = {
        "crossref": FakeClient(
            "crossref",
            _crossref_json(doi="10.9999/other", title="Wrong Provider Title"),
        ),
    }
    result = _enrich(paper_id, clients)

    assert result.status == "partial"
    with SessionLocal() as session:
        record = session.execute(
            select(DOIProviderResult).where(
                DOIProviderResult.paper_id == paper_id,
                DOIProviderResult.provider == "crossref",
            )
        ).scalars().first()
        assert record is not None
        assert record.error_code == "doi_mismatch"
        assert "10.9999/other" in (record.error_message or "")

    provider_candidates = [
        c for c in _candidates(paper_id)
        if c.source_type == PROVIDER_SOURCE_TYPE and c.source_location == "crossref"
    ]
    assert provider_candidates == []
    with SessionLocal() as session:
        paper = session.get(Paper, paper_id)
        assert paper.title is None  # nothing was adopted from the mismatched provider
        assert paper.normalized_title is None


# ---------------------------------------------------------------------------
# Legacy author candidates + no-aggregate authors guard
# ---------------------------------------------------------------------------


def test_legacy_author_candidates_preserved_and_rows_not_erased_without_aggregate():
    paper_id, file_id = _make_paper_with_primary_file()
    _add_heuristic_state(paper_id, file_id)

    # Provider supplies no authors: no aggregate authors candidate exists.
    clients = {
        "crossref": FakeClient(
            "crossref",
            _crossref_json(title="Provider Title"),
        ),
    }
    _enrich(paper_id, clients)

    # Legacy field_name=author candidates remain readable, unmodified.
    with SessionLocal() as session:
        rows = _candidates(paper_id)
        legacy = [c for c in rows if c.field_name == "author"]
        assert {c.value_text for c in legacy} == {"Bob Smith", "Carol Jones"}
        # Existing PaperAuthor rows are NOT erased without an aggregate candidate.
        assert _authors(paper_id) == ["Bob Smith", "Carol Jones"]
        assert _author_orders(paper_id) == [1, 2]


# ---------------------------------------------------------------------------
# Manual update API: authors validation + audit + legacy no-file path
# ---------------------------------------------------------------------------


def test_manual_authors_round_trip_and_audit():
    paper_id, _ = _make_paper_with_primary_file()
    result = update_paper_metadata(paper_id, {"authors": ["Alice A.", "Bob B."]})
    assert result.status == "updated"
    assert "authors" in result.updated_fields
    assert result.audit_log_id is not None
    assert _authors(paper_id) == ["Alice A.", "Bob B."]
    assert _author_orders(paper_id) == [1, 2]
    with SessionLocal() as session:
        manual = session.execute(
            select(MetadataCandidate).where(
                MetadataCandidate.paper_id == paper_id,
                MetadataCandidate.field_name == "authors",
                MetadataCandidate.source_type == MANUAL_SOURCE_TYPE,
            )
        ).scalars().all()
        assert len(manual) == 1
        assert json.loads(manual[0].value_text) == ["Alice A.", "Bob B."]
        log = session.get(AuditLog, result.audit_log_id)
        assert log.action == "update_metadata"
        assert json.loads(log.new_value_json)["authors"] == ["Alice A.", "Bob B."]


def test_manual_update_rejects_invalid_authors():
    paper_id, _ = _make_paper_with_primary_file()
    result = update_paper_metadata(paper_id, {"authors": "not a list"})
    assert result.status == "failed"
    assert result.error_code == INVALID_FIELDS
    result = update_paper_metadata(paper_id, {"authors": []})
    assert result.status == "failed"
    assert result.error_code == INVALID_FIELDS
    result = update_paper_metadata(paper_id, {"authors": ["", "  "]})
    assert result.status == "failed"
    assert result.error_code == INVALID_FIELDS


def test_manual_update_without_primary_file_keeps_legacy_behavior():
    paper_id = _make_fileless_paper()
    result = update_paper_metadata(paper_id, {"title": "Legacy Title"})
    assert result.status == "updated"
    assert result.audit_log_id is not None
    with SessionLocal() as session:
        paper = session.get(Paper, paper_id)
        assert paper.title == "Legacy Title"
        assert paper.normalized_title == "legacy title"
        # No candidates are fabricated without a primary file.
        assert session.query(MetadataCandidate).count() == 0


def test_no_primary_file_fails_clearly():
    paper_id = _make_fileless_paper()
    with SessionLocal() as session:
        with pytest.raises(NoPrimaryFileError):
            get_primary_file(session, paper_id)
        with pytest.raises(NoPrimaryFileError):
            get_primary_file(session, "missingpaperid1234567890abcdefghijk")


# ---------------------------------------------------------------------------
# Idempotent manual updates create no duplicate candidates
# ---------------------------------------------------------------------------


def test_repeated_identical_manual_update_creates_no_duplicate_candidates():
    paper_id, _ = _make_paper_with_primary_file()
    update_paper_metadata(paper_id, {"title": "Stable Manual Title"})
    update_paper_metadata(paper_id, {"title": "Stable Manual Title"})
    with SessionLocal() as session:
        manual = session.execute(
            select(MetadataCandidate).where(
                MetadataCandidate.paper_id == paper_id,
                MetadataCandidate.field_name == "title",
                MetadataCandidate.source_type == MANUAL_SOURCE_TYPE,
            )
        ).scalars().all()
        assert len(manual) == 1
        assert session.query(AuditLog).count() == 2  # each update is audited once
