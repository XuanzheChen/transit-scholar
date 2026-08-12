"""Frozen result dataclasses and status constants for DOI enrichment.

These types are the stable boundary between the enrichment service, the
pipeline and the web API. No business logic lives here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


# --- Provider status enumeration ---------------------------------------------
# Single doi_provider_results.status values (see Phase 1 spec §5).
PROVIDER_PENDING = "pending"
PROVIDER_RUNNING = "running"
PROVIDER_FETCHED = "fetched"
PROVIDER_PARTIAL = "partial"
PROVIDER_NOT_FOUND = "not_found"
PROVIDER_SKIPPED = "skipped"
PROVIDER_RATE_LIMITED = "rate_limited"
PROVIDER_RETRY_SCHEDULED = "retry_scheduled"
PROVIDER_FAILED = "failed"


# --- Enrichment job status enumeration ---------------------------------------
# doi_enrichment_jobs.status values (see Phase 1 spec §6).
JOB_PENDING = "pending"
JOB_RUNNING = "running"
JOB_FETCHED = "fetched"
JOB_PARTIAL = "partial"
JOB_BLOCKED = "blocked"
JOB_SKIPPED = "skipped"
JOB_FAILED = "failed"


class ProviderStatus:
    """Namespace mirroring the provider/job status string constants."""

    PENDING = PROVIDER_PENDING
    RUNNING = PROVIDER_RUNNING
    FETCHED = PROVIDER_FETCHED
    PARTIAL = PROVIDER_PARTIAL
    NOT_FOUND = PROVIDER_NOT_FOUND
    SKIPPED = PROVIDER_SKIPPED
    RATE_LIMITED = PROVIDER_RATE_LIMITED
    RETRY_SCHEDULED = PROVIDER_RETRY_SCHEDULED
    FAILED = PROVIDER_FAILED


@dataclass
class ProviderFetchResult:
    """Structured outcome of one ProviderClient.fetch() call.

    ``raw_json`` holds the provider's raw response text; ``retry_after`` is
    parsed from a Retry-After header when present.
    """

    provider: str
    status: str
    http_status: int | None = None
    raw_json: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    retry_after: datetime | None = None


@dataclass
class ParsedAuthor:
    """One author parsed from a provider response."""

    full_name: str
    orcid: str | None = None
    affiliation: str | None = None


@dataclass
class ParsedFields:
    """Unified candidate fields extracted from a provider's raw JSON."""

    title: str | None = None
    authors: list[ParsedAuthor] = field(default_factory=list)
    publication_year: int | None = None
    venue: str | None = None
    publisher: str | None = None
    abstract: str | None = None
    doi: str | None = None
    arxiv_id: str | None = None
    source_type: str = "doi_provider"
    source_location: str = ""


@dataclass
class ProviderResult:
    """One provider's result as surfaced to the web API / pipeline.

    ``error_message`` is a non-secret, human-readable failure description
    (API keys and unsafe request URLs/headers are redacted before it is set).
    """

    provider: str
    status: str
    http_status: int | None = None
    fetched_at: datetime | None = None
    attempt_count: int = 0
    next_retry_at: datetime | None = None
    error_code: str | None = None
    fields: list[str] = field(default_factory=list)
    error_message: str | None = None


@dataclass
class EnrichmentJobResult:
    """Outcome of one enrich_paper_by_doi() / refresh_enrichment() call."""

    paper_id: str
    doi: str | None
    status: str
    providers: list[ProviderResult] = field(default_factory=list)
    resolved: dict[str, str] = field(default_factory=dict)
    error_code: str | None = None
    error_message: str | None = None
