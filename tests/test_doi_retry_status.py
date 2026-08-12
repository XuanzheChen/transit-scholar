"""DOI provider retry, persisted outcome, and query observability tests.

Covers AC-DOI-003 through AC-DOI-007: Retry-After parsing (delta seconds and
RFC HTTP-date), bounded exponential backoff with 10 percent jitter, the four
attempt cap, the distinct persisted provider outcomes, API-visible
error_message/fields, the OpenAlex keyless cascade continuation, stale-field
clearing, and secret redaction.

Zero real network: every provider fetch goes through a scripted fake client or
a patched ``urlopen``; the module-level clock/random seams make Retry-After
and backoff assertions deterministic.
"""

from __future__ import annotations

import importlib
import json
from datetime import datetime, timedelta, timezone
from email.message import Message
from urllib.error import HTTPError, URLError

import pytest

from transit_scholar import config as _config
from transit_scholar.db.engine import SessionLocal
from transit_scholar.db.models import (
    DOIEnrichmentJob,
    DOIProviderResult,
    Paper,
    PaperAuthor,
)
from transit_scholar.doi_enrichment import service as doi_service
from transit_scholar.doi_enrichment.clients import base as base_module
from transit_scholar.doi_enrichment.clients.base import (
    ProviderClient,
    parse_retry_after,
    redact_secrets,
)
from transit_scholar.doi_enrichment.clients.crossref import CrossrefClient
from transit_scholar.doi_enrichment.clients.openalex import OpenAlexClient
from transit_scholar.doi_enrichment.clients.semantic_scholar import (
    SemanticScholarClient,
)
from transit_scholar.doi_enrichment.providers import ProviderRegistry
from transit_scholar.doi_enrichment.rate_limiter import SerialRateLimiter
from transit_scholar.doi_enrichment.result import (
    PROVIDER_FETCHED,
    PROVIDER_NOT_FOUND,
    ProviderFetchResult,
)
from transit_scholar.doi_enrichment.service import enrich_paper_by_doi
from transit_scholar.web.app import enrichment

# Frozen clock origin for every deterministic time assertion.
T0 = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)

_CROSSREF_RAW = json.dumps({
    "message": {
        "DOI": "10.1000/example",
        "title": ["Retry Cascade Title"],
        "author": [{"given": "Ada", "family": "Lovelace"}],
        "issued": {"date-parts": [[2024]]},
    }
})


class Clock:
    """Fake module-level clock seam: returns ``now`` and can be advanced."""

    def __init__(self, start: datetime = T0) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now = self.now + timedelta(seconds=seconds)


class ScriptedClient(ProviderClient):
    """Provider client that replays scripted outcomes (last one repeats).

    With no outcomes it returns a benign 404, so a fake can be registered for
    a provider that is expected never to run without raising.
    """

    def __init__(self, name: str, outcomes: list[ProviderFetchResult]) -> None:
        self.name = name
        self.timeout_seconds = 1
        self.max_attempts = 4
        self.outcomes = list(outcomes)
        self.calls = 0

    def build_request(self, doi: str) -> tuple[str, dict[str, str]]:
        return f"https://example.invalid/{self.name}/{doi}", {
            "Accept": "application/json"
        }

    def fetch(self, doi: str) -> ProviderFetchResult:
        self.calls += 1
        if not self.outcomes:
            return ProviderFetchResult(
                provider=self.name,
                status=PROVIDER_NOT_FOUND,
                http_status=404,
                error_code="not_found",
            )
        return self.outcomes[min(self.calls - 1, len(self.outcomes) - 1)]


class _FakeResponse:
    """Minimal urllib response object for patched ``urlopen`` calls."""

    status = 200

    def __init__(self, body: bytes = b"{}", headers: Message | None = None) -> None:
        self._body = body
        self.headers = headers if headers is not None else Message()

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc) -> bool:
        return False


def _raise(exc: Exception):
    def _raiser(request, timeout=None):
        raise exc

    return _raiser


def _http_error(code: int, reason: str, retry_after: str | None = None) -> HTTPError:
    headers = Message()
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return HTTPError("https://example.invalid/", code, reason, headers, None)


def _http_outcome(code: int, reason: str) -> ProviderFetchResult:
    return ProviderFetchResult(
        provider="crossref",
        status="__network_error__",
        http_status=code,
        error_code=None,
        error_message=f"HTTP Error {code}: {reason}",
    )


_RATE_LIMITED_OUTCOME = _http_outcome(429, "Too Many Requests")
_TRANSIENT_OUTCOME = _http_outcome(503, "Service Unavailable")
_NON_RETRYABLE_OUTCOME = _http_outcome(400, "Bad Request")
_NETWORK_OUTCOME = ProviderFetchResult(
    provider="crossref",
    status="__network_error__",
    http_status=None,
    error_code="network_error",
    error_message="urlopen error [Errno -2] Name or service not known",
)
_NOT_FOUND_OUTCOME = ProviderFetchResult(
    provider="crossref",
    status=PROVIDER_NOT_FOUND,
    http_status=404,
    error_code="not_found",
)
_FETCHED_OUTCOME = ProviderFetchResult(
    provider="crossref",
    status=PROVIDER_FETCHED,
    http_status=200,
    raw_json=_CROSSREF_RAW,
)

_CLIENT_MODULES = {
    "crossref": "transit_scholar.doi_enrichment.clients.crossref",
    "openalex": "transit_scholar.doi_enrichment.clients.openalex",
    "semantic_scholar": "transit_scholar.doi_enrichment.clients.semantic_scholar",
}
_CLIENTS = {
    "crossref": CrossrefClient(),
    "openalex": OpenAlexClient(),
    "semantic_scholar": SemanticScholarClient(),
}


@pytest.fixture(autouse=True)
def _reset_database():
    with SessionLocal() as session:
        session.query(DOIProviderResult).delete()
        session.query(DOIEnrichmentJob).delete()
        session.query(PaperAuthor).delete()
        session.query(Paper).delete()
        session.commit()
    yield


@pytest.fixture(autouse=True)
def _restore_settings():
    """Restore mutable module-level settings after every test."""
    snapshot = {
        "openalex_api_key": _config.settings.openalex_api_key,
        "semantic_scholar_api_key": _config.settings.semantic_scholar_api_key,
        "network": _config.settings.metadata_enrichment_allow_network,
    }
    yield
    _config.settings.openalex_api_key = snapshot["openalex_api_key"]
    _config.settings.semantic_scholar_api_key = snapshot["semantic_scholar_api_key"]
    _config.settings.metadata_enrichment_allow_network = snapshot["network"]


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


def _enable_network_and_keyless(monkeypatch) -> None:
    monkeypatch.setattr(_config.settings, "metadata_enrichment_allow_network", True)
    monkeypatch.setattr(_config.settings, "openalex_api_key", None)
    monkeypatch.setattr(_config.settings, "semantic_scholar_api_key", None)


def _registry(crossref: ScriptedClient, semantic: ScriptedClient) -> ProviderRegistry:
    return ProviderRegistry(clients={
        "crossref": crossref,
        "openalex": ScriptedClient("openalex", []),
        "semantic_scholar": semantic,
    })


# --- AC-DOI-003: Retry-After parsing -----------------------------------------


def test_parse_retry_after_delta_seconds_to_absolute_utc(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(base_module, "_utcnow", clock)
    parsed = parse_retry_after("120")
    assert parsed == T0 + timedelta(seconds=120)
    assert parsed.tzinfo is not None
    assert parsed.utcoffset() == timedelta(0)
    assert parse_retry_after(" 30 ") == T0 + timedelta(seconds=30)
    assert parse_retry_after("0") == T0


def test_parse_retry_after_rfc_http_date_forms():
    expected = datetime(2015, 10, 21, 7, 28, 0, tzinfo=timezone.utc)
    # IMF-fixdate, RFC 850, asctime, and a non-UTC offset normalized to UTC.
    assert parse_retry_after("Wed, 21 Oct 2015 07:28:00 GMT") == expected
    assert parse_retry_after("Wednesday, 21-Oct-15 07:28:00 GMT") == expected
    assert parse_retry_after("Wed Oct 21 07:28:00 2015") == expected
    assert parse_retry_after("Wed, 21 Oct 2015 08:28:00 +0100") == expected


def test_parse_retry_after_invalid_values_fall_back_to_none(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(base_module, "_utcnow", clock)
    for value in (None, "", "   ", "abc", "-5", "30.5", "99999999999999999999999999"):
        assert parse_retry_after(value) is None


@pytest.mark.parametrize("name", sorted(_CLIENTS))
def test_clients_capture_retry_after_from_success_response(monkeypatch, name):
    module = importlib.import_module(_CLIENT_MODULES[name])
    clock = Clock()
    monkeypatch.setattr(base_module, "_utcnow", clock)
    headers = Message()
    headers["Retry-After"] = "45"
    monkeypatch.setattr(module, "urlopen", lambda request, timeout: _FakeResponse(b"{}", headers))
    result = _CLIENTS[name].fetch("10.1000/example")
    assert result.status == PROVIDER_FETCHED
    assert result.http_status == 200
    assert result.retry_after == T0 + timedelta(seconds=45)


@pytest.mark.parametrize("name", sorted(_CLIENTS))
def test_clients_capture_retry_after_from_http_error(monkeypatch, name):
    module = importlib.import_module(_CLIENT_MODULES[name])
    clock = Clock()
    monkeypatch.setattr(base_module, "_utcnow", clock)
    err = _http_error(429, "Too Many Requests", retry_after="90")
    monkeypatch.setattr(module, "urlopen", _raise(err))
    result = _CLIENTS[name].fetch("10.1000/example")
    assert result.http_status == 429
    assert result.error_code is None
    assert result.error_message == "HTTP Error 429: Too Many Requests"
    assert result.retry_after == T0 + timedelta(seconds=90)


@pytest.mark.parametrize("name", sorted(_CLIENTS))
def test_clients_preserve_404_as_not_found(monkeypatch, name):
    module = importlib.import_module(_CLIENT_MODULES[name])
    err = _http_error(404, "Not Found")
    monkeypatch.setattr(module, "urlopen", _raise(err))
    result = _CLIENTS[name].fetch("10.1000/example")
    assert result.status == PROVIDER_NOT_FOUND
    assert result.http_status == 404
    assert result.error_code == "not_found"
    assert result.error_message is None


@pytest.mark.parametrize("name", sorted(_CLIENTS))
def test_clients_map_transport_failure_to_network_error(monkeypatch, name):
    module = importlib.import_module(_CLIENT_MODULES[name])
    err = URLError("urlopen error [Errno 11001] getaddrinfo failed")
    monkeypatch.setattr(module, "urlopen", _raise(err))
    result = _CLIENTS[name].fetch("10.1000/example")
    assert result.status == "__network_error__"
    assert result.http_status is None
    assert result.error_code == "network_error"
    assert "getaddrinfo failed" in (result.error_message or "")
    assert result.retry_after is None


# --- AC-DOI-004: bounded fallback backoff and max four attempts --------------


@pytest.mark.parametrize(("attempt", "base"), [(1, 30), (2, 120), (3, 600), (4, 3600)])
def test_fallback_backoff_all_four_delays_with_10_percent_jitter(
    monkeypatch, attempt, base
):
    clock = Clock()
    monkeypatch.setattr(doi_service, "_utcnow", clock)
    # Return the received lower bound first, then the upper bound, so the fake
    # uniform drives _jitter to exactly -10 percent and +10 percent of base.
    bounds = iter([-1, 1])
    monkeypatch.setattr(
        doi_service.random,
        "uniform",
        lambda lo, hi: hi if next(bounds) > 0 else lo,
    )
    low = doi_service._next_retry_at(attempt, None)
    high = doi_service._next_retry_at(attempt, None)
    assert (low - T0).total_seconds() == pytest.approx(base * 0.9)
    assert (high - T0).total_seconds() == pytest.approx(base * 1.1)


def test_retry_after_precedence_over_fallback_backoff(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(doi_service, "_utcnow", clock)
    retry_at = T0 + timedelta(seconds=45)
    assert doi_service._next_retry_at(1, retry_at) == retry_at
    assert doi_service._next_retry_at(4, retry_at) == retry_at


def test_max_four_attempts_then_failed_and_forced_refresh_resets_budget(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(doi_service, "_utcnow", clock)
    _enable_network_and_keyless(monkeypatch)
    paper_id = _make_paper()
    crossref = ScriptedClient("crossref", [_TRANSIENT_OUTCOME])
    registry = _registry(crossref, ScriptedClient("semantic_scholar", []))
    limiter = SerialRateLimiter({})

    for attempt, base in ((1, 30), (2, 120), (3, 600)):
        result = enrich_paper_by_doi(paper_id, registry=registry, rate_limiter=limiter)
        provider = next(p for p in result.providers if p.provider == "crossref")
        assert provider.status == "retry_scheduled"
        assert provider.error_code == "transient_error"
        assert provider.attempt_count == attempt
        elapsed = (provider.next_retry_at - clock.now).total_seconds()
        assert base * 0.9 - 0.1 <= elapsed <= base * 1.1 + 0.1
        clock.advance(4000)

    # Fourth attempt exhausts the budget.
    result = enrich_paper_by_doi(paper_id, registry=registry, rate_limiter=limiter)
    provider = next(p for p in result.providers if p.provider == "crossref")
    assert provider.status == "failed"
    assert provider.error_code == "transient_error"
    assert provider.attempt_count == 4
    assert provider.next_retry_at is None

    # A further non-forced call makes no request at all.
    result = enrich_paper_by_doi(paper_id, registry=registry, rate_limiter=limiter)
    provider = next(p for p in result.providers if p.provider == "crossref")
    assert provider.status == "failed"
    assert provider.attempt_count == 4
    assert crossref.calls == 4
    with SessionLocal() as session:
        record = session.query(DOIProviderResult).filter_by(provider="crossref").one()
        assert record.attempt_count == 4

    # A forced refresh starts a fresh budget and consumes a new attempt.
    result = enrich_paper_by_doi(
        paper_id, registry=registry, rate_limiter=limiter, force=True
    )
    provider = next(p for p in result.providers if p.provider == "crossref")
    assert provider.attempt_count == 1
    assert provider.status == "retry_scheduled"
    assert crossref.calls == 5
    with SessionLocal() as session:
        record = session.query(DOIProviderResult).filter_by(provider="crossref").one()
        assert record.attempt_count == 1
        assert record.status == "retry_scheduled"


def test_non_forced_call_does_not_fetch_while_retry_scheduled(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(doi_service, "_utcnow", clock)
    _enable_network_and_keyless(monkeypatch)
    paper_id = _make_paper()
    crossref = ScriptedClient("crossref", [_RATE_LIMITED_OUTCOME])
    registry = _registry(crossref, ScriptedClient("semantic_scholar", []))
    limiter = SerialRateLimiter({})

    enrich_paper_by_doi(paper_id, registry=registry, rate_limiter=limiter)
    assert crossref.calls == 1

    # Same clock: the persisted schedule is still in the future.
    result = enrich_paper_by_doi(paper_id, registry=registry, rate_limiter=limiter)
    provider = next(p for p in result.providers if p.provider == "crossref")
    assert provider.status == "retry_scheduled"
    assert provider.error_code == "rate_limited"
    assert provider.attempt_count == 1
    assert crossref.calls == 1


def test_invalid_retry_after_falls_back_to_bounded_backoff(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(doi_service, "_utcnow", clock)
    _enable_network_and_keyless(monkeypatch)
    paper_id = _make_paper()
    # A 429 whose Retry-After header was unparseable (None) uses the backoff.
    crossref = ScriptedClient("crossref", [_RATE_LIMITED_OUTCOME])
    registry = _registry(crossref, ScriptedClient("semantic_scholar", []))
    result = enrich_paper_by_doi(paper_id, registry=registry, rate_limiter=SerialRateLimiter({}))
    provider = next(p for p in result.providers if p.provider == "crossref")
    elapsed = (provider.next_retry_at - T0).total_seconds()
    assert 30 * 0.9 - 0.1 <= elapsed <= 30 * 1.1 + 0.1


def test_retry_after_drives_persisted_next_retry_at(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(doi_service, "_utcnow", clock)
    monkeypatch.setattr(base_module, "_utcnow", clock)
    _enable_network_and_keyless(monkeypatch)
    paper_id = _make_paper()
    outcome = ProviderFetchResult(
        provider="crossref",
        status="__network_error__",
        http_status=429,
        error_code=None,
        error_message="HTTP Error 429: Too Many Requests",
        retry_after=parse_retry_after("45"),
    )
    crossref = ScriptedClient("crossref", [outcome])
    registry = _registry(crossref, ScriptedClient("semantic_scholar", []))
    result = enrich_paper_by_doi(paper_id, registry=registry, rate_limiter=SerialRateLimiter({}))
    provider = next(p for p in result.providers if p.provider == "crossref")
    assert provider.error_code == "rate_limited"
    assert provider.next_retry_at == T0 + timedelta(seconds=45)
    with SessionLocal() as session:
        record = session.query(DOIProviderResult).filter_by(provider="crossref").one()
        # SQLite stores naive datetimes; the persisted wall-clock value matches.
        assert record.next_retry_at == (T0 + timedelta(seconds=45)).replace(tzinfo=None)


# --- AC-DOI-005: distinct persisted outcomes ---------------------------------


@pytest.mark.parametrize(
    ("outcome", "expected_status", "expected_error_code", "expected_http", "expects_schedule"),
    [
        (_RATE_LIMITED_OUTCOME, "retry_scheduled", "rate_limited", 429, True),
        (_TRANSIENT_OUTCOME, "retry_scheduled", "transient_error", 503, True),
        (_NON_RETRYABLE_OUTCOME, "failed", "non_retryable_http", 400, False),
        (_NETWORK_OUTCOME, "failed", "network_error", None, False),
        (_NOT_FOUND_OUTCOME, "not_found", "not_found", 404, False),
    ],
)
def test_distinct_outcomes_persisted_and_surfaced(
    monkeypatch, outcome, expected_status, expected_error_code, expected_http, expects_schedule
):
    clock = Clock()
    monkeypatch.setattr(doi_service, "_utcnow", clock)
    _enable_network_and_keyless(monkeypatch)
    paper_id = _make_paper()
    crossref = ScriptedClient("crossref", [outcome])
    registry = _registry(crossref, ScriptedClient("semantic_scholar", []))
    result = enrich_paper_by_doi(paper_id, registry=registry, rate_limiter=SerialRateLimiter({}))

    provider = next(p for p in result.providers if p.provider == "crossref")
    assert provider.status == expected_status
    assert provider.error_code == expected_error_code
    assert provider.http_status == expected_http
    assert provider.attempt_count == 1
    if expects_schedule:
        assert provider.next_retry_at is not None
    else:
        assert provider.next_retry_at is None
    if outcome.error_message:
        assert provider.error_message == outcome.error_message

    with SessionLocal() as session:
        record = session.query(DOIProviderResult).filter_by(provider="crossref").one()
        assert record.status == expected_status
        assert record.error_code == expected_error_code
        assert record.http_status == expected_http
        assert record.attempt_count == 1
        assert record.error_message == outcome.error_message
        if expects_schedule:
            assert record.next_retry_at is not None
        else:
            assert record.next_retry_at is None


def test_network_disabled_persists_pending_without_request(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(doi_service, "_utcnow", clock)
    monkeypatch.setattr(_config.settings, "metadata_enrichment_allow_network", False)
    monkeypatch.setattr(_config.settings, "openalex_api_key", None)
    monkeypatch.setattr(_config.settings, "semantic_scholar_api_key", None)
    paper_id = _make_paper()
    crossref = ScriptedClient("crossref", [_FETCHED_OUTCOME])
    semantic = ScriptedClient("semantic_scholar", [_FETCHED_OUTCOME])
    openalex = ScriptedClient("openalex", [_FETCHED_OUTCOME])
    registry = ProviderRegistry(clients={
        "crossref": crossref,
        "openalex": openalex,
        "semantic_scholar": semantic,
    })
    result = enrich_paper_by_doi(paper_id, registry=registry, rate_limiter=SerialRateLimiter({}))

    assert crossref.calls == 0
    assert semantic.calls == 0
    assert openalex.calls == 0
    for name in ("crossref", "semantic_scholar"):
        provider = next(p for p in result.providers if p.provider == name)
        assert provider.status == "pending"
        assert provider.error_code == "network_disabled"
        assert provider.attempt_count == 0
        assert provider.next_retry_at is None
        with SessionLocal() as session:
            record = session.query(DOIProviderResult).filter_by(provider=name).one()
            assert record.status == "pending"
            assert record.error_code == "network_disabled"
            assert record.error_message == (
                "Metadata enrichment network access is disabled."
            )
            assert record.next_retry_at is None
    # The keyless OpenAlex skip is independent of the network gate.
    openalex_provider = next(p for p in result.providers if p.provider == "openalex")
    assert openalex_provider.status == "skipped"
    assert openalex_provider.error_code == "missing_api_key"


# --- AC-DOI-006 / AC-DOI-007: API observability and cascade continuation -----


def test_openalex_keyless_skip_while_other_providers_continue(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(doi_service, "_utcnow", clock)
    _enable_network_and_keyless(monkeypatch)
    paper_id = _make_paper()
    crossref = ScriptedClient("crossref", [_FETCHED_OUTCOME])
    openalex = ScriptedClient("openalex", [_FETCHED_OUTCOME])
    semantic = ScriptedClient("semantic_scholar", [_NOT_FOUND_OUTCOME])
    registry = ProviderRegistry(clients={
        "crossref": crossref,
        "openalex": openalex,
        "semantic_scholar": semantic,
    })
    result = enrich_paper_by_doi(paper_id, registry=registry, rate_limiter=SerialRateLimiter({}))

    assert crossref.calls == 1
    assert openalex.calls == 0
    assert semantic.calls == 1

    openalex_provider = next(p for p in result.providers if p.provider == "openalex")
    assert openalex_provider.status == "skipped"
    assert openalex_provider.error_code == "missing_api_key"
    assert openalex_provider.error_message is not None
    with SessionLocal() as session:
        record = session.query(DOIProviderResult).filter_by(provider="openalex").one()
        assert record.status == "skipped"
        assert record.error_code == "missing_api_key"
        assert record.attempt_count == 0

    crossref_provider = next(p for p in result.providers if p.provider == "crossref")
    assert crossref_provider.status == "fetched"
    assert crossref_provider.attempt_count == 1
    assert crossref_provider.fields == ["title", "authors", "publication_year", "doi"]


def test_success_clears_stale_error_and_retry_fields(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(doi_service, "_utcnow", clock)
    _enable_network_and_keyless(monkeypatch)
    paper_id = _make_paper()
    crossref = ScriptedClient("crossref", [_TRANSIENT_OUTCOME, _FETCHED_OUTCOME])
    registry = _registry(crossref, ScriptedClient("semantic_scholar", []))
    limiter = SerialRateLimiter({})

    result = enrich_paper_by_doi(paper_id, registry=registry, rate_limiter=limiter)
    provider = next(p for p in result.providers if p.provider == "crossref")
    assert provider.status == "retry_scheduled"
    assert provider.error_code == "transient_error"
    assert provider.error_message == "HTTP Error 503: Service Unavailable"
    assert provider.next_retry_at is not None

    clock.advance(4000)
    result = enrich_paper_by_doi(
        paper_id, registry=registry, rate_limiter=limiter, force=True
    )
    provider = next(p for p in result.providers if p.provider == "crossref")
    assert provider.status == "fetched"
    assert provider.error_code is None
    assert provider.error_message is None
    assert provider.next_retry_at is None
    assert provider.attempt_count == 2
    assert provider.fields == ["title", "authors", "publication_year", "doi"]
    with SessionLocal() as session:
        record = session.query(DOIProviderResult).filter_by(provider="crossref").one()
        assert record.status == "fetched"
        assert record.error_code is None
        assert record.error_message is None
        assert record.next_retry_at is None
        assert record.raw_json == _CROSSREF_RAW


def test_enrichment_api_exposes_all_persisted_facts(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(doi_service, "_utcnow", clock)
    _enable_network_and_keyless(monkeypatch)
    # Configure a secret so the API output can be proven free of it; the key
    # does not change the cascade because the scripted Semantic Scholar fake
    # never sends it and the service strips request headers before persisting.
    monkeypatch.setattr(_config.settings, "semantic_scholar_api_key", "S2_TOPSECRET")
    paper_id = _make_paper()
    crossref = ScriptedClient("crossref", [_TRANSIENT_OUTCOME])
    registry = _registry(crossref, ScriptedClient("semantic_scholar", [_NOT_FOUND_OUTCOME]))
    enrich_paper_by_doi(paper_id, registry=registry, rate_limiter=SerialRateLimiter({}))

    data = enrichment(paper_id)
    providers = {p["provider"]: p for p in data["providers"]}
    required_keys = {
        "provider", "status", "http_status", "error_code", "error_message",
        "fields", "attempt_count", "next_retry_at",
    }
    for provider_name in ("crossref", "openalex", "semantic_scholar"):
        assert required_keys <= providers[provider_name].keys()

    crossref_view = providers["crossref"]
    assert crossref_view["status"] == "retry_scheduled"
    assert crossref_view["http_status"] == 503
    assert crossref_view["error_code"] == "transient_error"
    assert crossref_view["error_message"] == "HTTP Error 503: Service Unavailable"
    assert crossref_view["attempt_count"] == 1
    assert isinstance(crossref_view["next_retry_at"], str)
    assert crossref_view["fields"] == []

    assert providers["semantic_scholar"]["status"] == "not_found"
    assert providers["semantic_scholar"]["error_code"] == "not_found"
    assert providers["openalex"]["status"] == "skipped"
    assert providers["openalex"]["error_code"] == "missing_api_key"

    serialized = json.dumps(data)
    # missing_api_key is an approved public error code and must remain
    # visible; only configured secret values and unsafe query material
    # (api_key=<secret>) are forbidden from the API output.
    assert "missing_api_key" in serialized
    assert "api_key=" not in serialized
    assert "S2_TOPSECRET" not in serialized


def test_enrichment_api_lists_returned_fields_from_persisted_raw(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(doi_service, "_utcnow", clock)
    _enable_network_and_keyless(monkeypatch)
    paper_id = _make_paper()
    crossref = ScriptedClient("crossref", [_FETCHED_OUTCOME])
    registry = _registry(crossref, ScriptedClient("semantic_scholar", [_NOT_FOUND_OUTCOME]))
    enrich_paper_by_doi(paper_id, registry=registry, rate_limiter=SerialRateLimiter({}))

    data = enrichment(paper_id)
    crossref_view = next(p for p in data["providers"] if p["provider"] == "crossref")
    assert crossref_view["status"] == "fetched"
    assert crossref_view["error_message"] is None
    assert crossref_view["fields"] == ["title", "authors", "publication_year", "doi"]


# --- AC-DOI-007 / AC-GLOBAL-004: zero real network and secret redaction -----


def test_no_real_network_requests_are_made(monkeypatch):
    _enable_network_and_keyless(monkeypatch)
    for module_name in _CLIENT_MODULES.values():
        module = importlib.import_module(module_name)
        monkeypatch.setattr(
            module, "urlopen", lambda *args, **kwargs: pytest.fail("real network request")
        )
    paper_id = _make_paper()
    crossref = ScriptedClient("crossref", [_FETCHED_OUTCOME])
    registry = _registry(crossref, ScriptedClient("semantic_scholar", [_NOT_FOUND_OUTCOME]))
    result = enrich_paper_by_doi(paper_id, registry=registry, rate_limiter=SerialRateLimiter({}))
    assert next(p for p in result.providers if p.provider == "crossref").status == "fetched"


def test_redact_secrets_removes_keys_and_query_param(monkeypatch):
    monkeypatch.setattr(_config.settings, "openalex_api_key", "OPENALEX_TOPSECRET")
    monkeypatch.setattr(_config.settings, "semantic_scholar_api_key", "S2_TOPSECRET")
    text = "query https://api.openalex.org/works/doi:10.1/2?api_key=OPENALEX_TOPSECRET failed (S2_TOPSECRET)"
    cleaned = redact_secrets(text)
    assert "OPENALEX_TOPSECRET" not in cleaned
    assert "S2_TOPSECRET" not in cleaned
    assert "api_key=[REDACTED]" in cleaned
    assert redact_secrets(None) is None
    assert redact_secrets("") == ""


def test_client_error_message_never_contains_api_key(monkeypatch):
    monkeypatch.setattr(_config.settings, "openalex_api_key", "OPENALEX_TOPSECRET")
    module = importlib.import_module(_CLIENT_MODULES["openalex"])
    err = URLError(
        "urlopen error for https://api.openalex.org/works/doi:10.1/2?api_key=OPENALEX_TOPSECRET"
    )
    monkeypatch.setattr(module, "urlopen", _raise(err))
    result = OpenAlexClient().fetch("10.1000/example")
    assert result.error_code == "network_error"
    assert "OPENALEX_TOPSECRET" not in (result.error_message or "")
    assert "[REDACTED]" in (result.error_message or "")
