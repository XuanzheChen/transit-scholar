"""DOI enrichment service.

Creates/reuses a ``DOIEnrichmentJob`` for a paper, runs the provider cascade,
caches each provider's raw JSON, merges fields into the paper by priority, and
schedules retries on transient failures. Network access is gated by
``settings.metadata_enrichment_allow_network``; Phase 2 tests mock the clients.
"""

from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone
from typing import Iterable

from sqlalchemy import select

from transit_scholar.config import settings
from transit_scholar.db.engine import SessionLocal
from transit_scholar.db.models import (
    DOIEnrichmentJob,
    DOIProviderResult,
    Paper,
    PaperAuthor,
)
from transit_scholar.doi_enrichment.clients import OpenAlexClient
from transit_scholar.doi_enrichment.clients.base import ProviderClient
from transit_scholar.doi_enrichment.clients.openalex import sanitize_url
from transit_scholar.doi_enrichment.merger import (
    best_field,
    doi_consistent,
    minimum_fields_satisfied,
    parse_crossref,
    parse_openalex,
    parse_semantic_scholar,
)
from transit_scholar.metadata import selection as metadata_selection
from transit_scholar.doi_enrichment.providers import (
    PROVIDER_MAX_ATTEMPTS,
    ProviderRegistry,
    default_rate_limiter,
)
from transit_scholar.doi_enrichment.rate_limiter import SerialRateLimiter
from transit_scholar.doi_enrichment.result import (
    JOB_BLOCKED,
    JOB_FAILED,
    JOB_FETCHED,
    JOB_PARTIAL,
    JOB_PENDING,
    JOB_RUNNING,
    PROVIDER_FAILED,
    PROVIDER_FETCHED,
    PROVIDER_NOT_FOUND,
    PROVIDER_PARTIAL,
    PROVIDER_PENDING,
    PROVIDER_RATE_LIMITED,
    PROVIDER_RETRY_SCHEDULED,
    PROVIDER_RUNNING,
    PROVIDER_SKIPPED,
    EnrichmentJobResult,
    ParsedFields,
    ProviderFetchResult,
    ProviderResult,
)

# Retryable HTTP statuses (Phase 1 spec §10.1). 429 is classified as
# ``rate_limited``; the remaining values are classified as ``transient_error``.
_RETRYABLE_HTTP = {429, 500, 502, 503, 504}
# Non-retryable HTTP statuses (Phase 1 spec §10.2). The service classifies any
# other non-404 HTTP status (400/401/403/…) as ``non_retryable_http``; this
# frozen set documents the spec's canonical members.
_NON_RETRYABLE_HTTP = {400, 401, 403, 404}
# Exponential backoff schedule in seconds by attempt index (Phase 1 spec §10.3).
_BACKOFF_SECONDS = [30, 120, 600, 3600]
_JITTER_FRACTION = 0.1


def _utcnow() -> datetime:
    """Module-level clock seam; tests monkeypatch this to freeze time."""
    return datetime.now(timezone.utc)


def _as_aware(value: datetime | None) -> datetime | None:
    """Normalize a persisted datetime to timezone-aware UTC.

    SQLite stores datetimes without timezone information, so a value read back
    from a ``DOIProviderResult`` is naive even though it was written aware.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _jitter(base_seconds: float) -> float:
    """Plus/minus 10 percent jitter around a base delay (deterministic seam).

    Tests monkeypatch ``random.uniform`` (or ``_jitter``) for bounded
    assertions; production uses a uniform draw in [-0.1, 0.1] * base.
    """
    return random.uniform(-_JITTER_FRACTION, _JITTER_FRACTION) * base_seconds


def derive_enrichment_status(
    paper: Paper,
    job: DOIEnrichmentJob | None,
) -> str:
    """Derive the paper's enrichment status from its job (Phase 1 spec §2.3).

    This is a derived value, never a ``papers`` column.
    """
    if job is not None:
        return job.status
    if not paper.normalized_doi:
        return "skipped"
    return "pending"


def enrich_paper_by_doi(
    paper_id: str,
    *,
    registry: ProviderRegistry | None = None,
    rate_limiter: SerialRateLimiter | None = None,
    force: bool = False,
) -> EnrichmentJobResult:
    """Run (or reuse) DOI enrichment for a paper.

    Creates a ``DOIEnrichmentJob`` if needed, runs the provider cascade, caches
    raw JSON, merges fields and returns a structured result. When ``force`` is
    set, cached provider results are re-fetched.
    """
    if registry is None:
        registry = ProviderRegistry()
    if rate_limiter is None:
        rate_limiter = default_rate_limiter()

    with SessionLocal() as session:
        paper = session.get(Paper, paper_id)
        if paper is None:
            return EnrichmentJobResult(
                paper_id=paper_id,
                doi=None,
                status=JOB_FAILED,
                error_code="paper_not_found",
            )

        if not paper.normalized_doi:
            return EnrichmentJobResult(
                paper_id=paper_id,
                doi=None,
                status="skipped",
                error_code="stable_identifier_missing:doi",
            )

        job = _get_or_create_job(session, paper, force=force)
        doi = job.doi

    return _run_cascade(
        paper_id=paper_id,
        doi=doi,
        job=job,
        registry=registry,
        rate_limiter=rate_limiter,
        force=force,
    )


def refresh_enrichment(
    paper_id: str,
    *,
    registry: ProviderRegistry | None = None,
    rate_limiter: SerialRateLimiter | None = None,
) -> EnrichmentJobResult:
    """Manually re-run enrichment, ignoring cached provider results."""
    return enrich_paper_by_doi(
        paper_id,
        registry=registry,
        rate_limiter=rate_limiter,
        force=True,
    )


def _get_or_create_job(
    session, paper: Paper, *, force: bool
) -> DOIEnrichmentJob:
    existing = session.execute(
        select(DOIEnrichmentJob).where(DOIEnrichmentJob.paper_id == paper.id)
    ).scalar_one_or_none()
    if existing is not None and not force:
        return existing
    if existing is not None:
        # Re-open a finished job for re-fetch.
        existing.status = JOB_RUNNING
        existing.doi = paper.normalized_doi
        session.commit()
        return existing
    job = DOIEnrichmentJob(
        paper_id=paper.id,
        doi=paper.normalized_doi,
        status=JOB_RUNNING,
    )
    session.add(job)
    session.commit()
    return job


def _run_cascade(
    paper_id: str,
    doi: str,
    job: DOIEnrichmentJob,
    registry: ProviderRegistry,
    rate_limiter: SerialRateLimiter,
    *,
    force: bool,
) -> EnrichmentJobResult:
    """Execute the provider cascade and merge results."""
    clients = registry.ordered_clients()

    parsed: dict[str, ParsedFields] = {}
    provider_results: list[ProviderResult] = []
    any_success = False
    any_fields = False

    with SessionLocal() as session:
        job = session.get(DOIEnrichmentJob, job.id)
        for client in clients:
            result = _run_one_provider(
                session, job, client, rate_limiter, force=force
            )
            provider_results.append(result)
            if result.status == PROVIDER_SKIPPED:
                continue
            if result.status in (PROVIDER_FETCHED, PROVIDER_PARTIAL):
                any_success = True
            if result.fields:
                any_fields = True
            parsed[client.name] = _parse_for_provider(client.name, session, job, client)

        # OpenAlex skipped record (no key) — surface in results. The skip is
        # independent of the network gate: no request is ever attempted.
        if not settings.openalex_api_key:
            record = _get_or_create_record(session, job, "openalex")
            record.status = PROVIDER_SKIPPED
            record.error_code = "missing_api_key"
            record.error_message = (
                "OpenAlex API key is not configured; provider skipped."
            )
            record.request_url = ""
            record.request_headers_json = "{}"
            session.flush()
            provider_results.append(ProviderResult(
                provider="openalex",
                status=PROVIDER_SKIPPED,
                error_code="missing_api_key",
                error_message=record.error_message,
            ))

        # Merge fields into the paper.
        resolved = _merge_into_paper(session, job, parsed)

        # Determine job status.
        if minimum_fields_satisfied(resolved):
            job_status = JOB_FETCHED
        elif any_success or any_fields:
            job_status = JOB_PARTIAL
        elif all(
            r.status in (PROVIDER_FAILED, PROVIDER_NOT_FOUND)
            for r in provider_results
            if r.status != PROVIDER_SKIPPED
        ):
            job_status = JOB_BLOCKED
        else:
            job_status = JOB_PARTIAL

        job.status = job_status
        session.commit()

        return EnrichmentJobResult(
            paper_id=paper_id,
            doi=doi,
            status=job_status,
            providers=sorted(provider_results, key=lambda r: r.provider),
            resolved=resolved,
        )


def _run_one_provider(
    session,
    job: DOIEnrichmentJob,
    client: ProviderClient,
    rate_limiter: SerialRateLimiter,
    *,
    force: bool,
) -> ProviderResult:
    """Fetch one provider, persist its result, schedule retries on 429/5xx.

    One service invocation performs at most one request per provider; there is
    no in-call sleep/retry loop. Retries are represented by the persisted
    ``next_retry_at`` schedule: a non-forced call does not re-request while the
    schedule is still in the future, and an exhausted retry budget (four
    persisted attempts) blocks further non-forced requests until a forced
    refresh starts a fresh budget.
    """
    record = _get_or_create_record(session, job, client.name)
    max_attempts = PROVIDER_MAX_ATTEMPTS.get(client.name, client.max_attempts)

    # Cache hit: reuse raw_json unless a refresh is forced.
    if record.raw_json and not force and record.status == PROVIDER_FETCHED:
        fields = _fields_from_cached(record.raw_json, client.name)
        return ProviderResult(
            provider=client.name,
            status=record.status,
            http_status=record.http_status,
            fetched_at=record.fetched_at,
            attempt_count=record.attempt_count,
            next_retry_at=_as_aware(record.next_retry_at),
            error_code=record.error_code,
            error_message=record.error_message,
            fields=fields,
        )

    # A forced refresh starts a fresh retry budget once the old one is spent.
    if force and record.attempt_count >= max_attempts:
        record.attempt_count = 0
        record.status = PROVIDER_PENDING

    # Honour the persisted schedule: do not re-request before next_retry_at.
    scheduled = _as_aware(record.next_retry_at)
    if not force and scheduled is not None and scheduled > _utcnow():
        fields = (
            _fields_from_cached(record.raw_json, client.name)
            if record.raw_json
            else []
        )
        return ProviderResult(
            provider=client.name,
            status=record.status,
            http_status=record.http_status,
            fetched_at=record.fetched_at,
            attempt_count=record.attempt_count,
            next_retry_at=scheduled,
            error_code=record.error_code,
            error_message=record.error_message,
            fields=fields,
        )

    # Exhausted budget: no further non-forced requests.
    if not force and record.attempt_count >= max_attempts:
        fields = (
            _fields_from_cached(record.raw_json, client.name)
            if record.raw_json
            else []
        )
        return ProviderResult(
            provider=client.name,
            status=record.status,
            http_status=record.http_status,
            fetched_at=record.fetched_at,
            attempt_count=record.attempt_count,
            next_retry_at=_as_aware(record.next_retry_at),
            error_code=record.error_code,
            error_message=record.error_message,
            fields=fields,
        )

    if not settings.metadata_enrichment_allow_network:
        # Network disabled: mark running->pending so a later refresh can run.
        record.status = PROVIDER_PENDING
        record.error_code = "network_disabled"
        record.error_message = "Metadata enrichment network access is disabled."
        record.next_retry_at = None
        session.commit()
        return ProviderResult(
            provider=client.name,
            status=PROVIDER_PENDING,
            attempt_count=record.attempt_count,
            error_code="network_disabled",
            error_message=record.error_message,
        )

    record.status = PROVIDER_RUNNING
    record.request_url = _safe_url(client, job.doi)
    record.request_headers_json = json.dumps(_safe_headers(client))
    session.commit()

    rate_limiter.acquire(client.name)
    fetch: ProviderFetchResult | None = None
    try:
        fetch = client.fetch(job.doi)
    finally:
        rate_limiter.release(
            client.name, retry_after=fetch.retry_after if fetch else None
        )

    record.http_status = fetch.http_status
    record.attempt_count += 1

    if fetch.status == PROVIDER_FETCHED and fetch.raw_json:
        record.raw_json = fetch.raw_json
        record.status = PROVIDER_FETCHED
        record.fetched_at = _utcnow()
        record.error_code = None
        record.error_message = None
        record.next_retry_at = None
        fields = _fields_from_raw(client.name, fetch.raw_json)
    elif fetch.status == PROVIDER_NOT_FOUND:
        record.status = PROVIDER_NOT_FOUND
        record.error_code = "not_found"
        record.error_message = None
        record.next_retry_at = None
        fields = []
    elif fetch.http_status == 429:
        record.status = PROVIDER_RETRY_SCHEDULED
        record.error_code = "rate_limited"
        record.error_message = fetch.error_message
        record.next_retry_at = _next_retry_at(
            record.attempt_count, retry_after=fetch.retry_after
        )
        if record.attempt_count >= max_attempts:
            record.status = PROVIDER_FAILED
            record.next_retry_at = None
        fields = []
    elif fetch.http_status in _RETRYABLE_HTTP:
        record.status = PROVIDER_RETRY_SCHEDULED
        record.error_code = "transient_error"
        record.error_message = fetch.error_message
        record.next_retry_at = _next_retry_at(
            record.attempt_count, retry_after=fetch.retry_after
        )
        if record.attempt_count >= max_attempts:
            record.status = PROVIDER_FAILED
            record.next_retry_at = None
        fields = []
    elif fetch.http_status is not None:
        # Any other HTTP status (400/401/403/…) is a non-retryable failure.
        record.status = PROVIDER_FAILED
        record.error_code = "non_retryable_http"
        record.error_message = fetch.error_message
        record.next_retry_at = None
        fields = []
    else:
        # No HTTP status: a transport-level failure such as DNS or timeout.
        record.status = PROVIDER_FAILED
        record.error_code = "network_error"
        record.error_message = fetch.error_message
        record.next_retry_at = None
        fields = []

    session.commit()
    return ProviderResult(
        provider=client.name,
        status=record.status,
        http_status=record.http_status,
        fetched_at=record.fetched_at,
        attempt_count=record.attempt_count,
        next_retry_at=_as_aware(record.next_retry_at),
        error_code=record.error_code,
        error_message=record.error_message,
        fields=fields,
    )


def _get_or_create_record(
    session, job: DOIEnrichmentJob, provider: str
) -> DOIProviderResult:
    existing = session.execute(
        select(DOIProviderResult).where(
            DOIProviderResult.job_id == job.id,
            DOIProviderResult.provider == provider,
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    record = DOIProviderResult(
        job_id=job.id,
        paper_id=job.paper_id,
        doi=job.doi,
        provider=provider,
        status=PROVIDER_PENDING,
        request_url="",
    )
    session.add(record)
    session.commit()
    return record


def _safe_url(client: ProviderClient, doi: str) -> str:
    url, _ = client.build_request(doi)
    if isinstance(client, OpenAlexClient):
        return sanitize_url(url)
    return url


def _safe_headers(client: ProviderClient) -> dict[str, str]:
    _, headers = client.build_request("")
    return headers


def _parse_for_provider(
    provider: str,
    session,
    job: DOIEnrichmentJob,
    client: ProviderClient,
) -> ParsedFields:
    record = session.execute(
        select(DOIProviderResult).where(
            DOIProviderResult.job_id == job.id,
            DOIProviderResult.provider == provider,
        )
    ).scalar_one_or_none()
    if record is None or not record.raw_json:
        return ParsedFields(source_location=provider)
    return _fields_from_raw(provider, record.raw_json, return_parsed=True)  # type: ignore[return-value]


def _fields_from_cached(raw_json: str, provider: str) -> list[str]:
    parsed = _parse_raw(provider, raw_json)
    return _present_fields(parsed)


def _fields_from_raw(
    provider: str, raw_json: str, *, return_parsed: bool = False
):
    parsed = _parse_raw(provider, raw_json)
    if return_parsed:
        return parsed
    return _present_fields(parsed)


def _parse_raw(provider: str, raw_json: str) -> ParsedFields:
    if provider == "crossref":
        return parse_crossref(raw_json)
    if provider == "openalex":
        return parse_openalex(raw_json)
    if provider == "semantic_scholar":
        return parse_semantic_scholar(raw_json)
    return ParsedFields(source_location=provider)


def _present_fields(parsed: ParsedFields) -> list[str]:
    fields = []
    if parsed.title:
        fields.append("title")
    if parsed.authors:
        fields.append("authors")
    if parsed.publication_year:
        fields.append("publication_year")
    if parsed.venue:
        fields.append("venue")
    if parsed.publisher:
        fields.append("publisher")
    if parsed.abstract:
        fields.append("abstract")
    if parsed.doi:
        fields.append("doi")
    if parsed.arxiv_id:
        fields.append("arxiv_id")
    return fields


def _merge_into_paper(
    session, job: DOIEnrichmentJob, parsed: dict[str, ParsedFields]
) -> dict[str, str]:
    """Merge provider fields into the paper. Returns the resolved source map.

    When the paper has a non-deleted primary file, every usable provider
    scalar is persisted as a ``doi_provider`` candidate on that file, the
    deterministic selection service picks at most one candidate per logical
    field (manual_confirmed > doi_provider > heuristic), and the selected
    values are materialized. This lets provider values replace selected
    heuristic values while never overriding manual_confirmed values. Papers
    without a primary file keep the legacy in-place merge so nothing is
    fabricated and no schema value is invented.

    The resolved map represents the paper's current usable field state, not
    only fields newly written during this call. This keeps cached re-runs from
    downgrading a previously fetched job to partial just because there was
    nothing new to update.
    """
    paper = session.get(Paper, job.paper_id)
    if paper is None:
        return {}

    mismatched = _record_doi_mismatches(session, job, paper, parsed)

    try:
        primary = metadata_selection.get_primary_file(session, paper.id)
    except metadata_selection.NoPrimaryFileError:
        primary = None
    if primary is None:
        return _legacy_direct_merge(session, paper, parsed)

    metadata_selection.persist_provider_candidates(
        session, paper, primary, parsed, skip_providers=mismatched
    )
    # The engine uses autoflush=False, so the fresh candidates must be flushed
    # before selection can see them.
    session.flush()
    selected = metadata_selection.select_candidates(session, paper.id)
    materialized = metadata_selection.materialize_selection(session, paper, selected)

    session.flush()
    resolved: dict[str, str] = {
        field: _source_label(selected[field]) for field in materialized
    }
    if paper.title:
        resolved.setdefault("title", "existing")
    if paper.publication_year:
        resolved.setdefault("publication_year", "existing")
    if paper.venue:
        resolved.setdefault("venue", "existing")
    if paper.abstract:
        resolved.setdefault("abstract", "existing")
    if _paper_has_authors(session, paper.id):
        resolved.setdefault("authors", "existing")
    if paper.doi or paper.normalized_doi:
        resolved.setdefault("doi", "local")
    if paper.arxiv_id:
        resolved.setdefault("arxiv_id", "existing")
    return resolved


def _record_doi_mismatches(
    session,
    job: DOIEnrichmentJob,
    paper: Paper,
    parsed: dict[str, ParsedFields],
) -> set[str]:
    """Persist a ``doi_mismatch`` provider fact for every provider whose DOI
    differs from the paper's normalized DOI after normalization on both sides.

    Returns the set of mismatched provider keys; those providers contribute
    zero metadata candidates.
    """
    mismatched: set[str] = set()
    normalized_doi = paper.normalized_doi
    if not normalized_doi:
        return mismatched
    for provider, fields in parsed.items():
        if not fields.doi:
            continue
        if doi_consistent(fields.doi, normalized_doi):
            record = _get_or_create_record(session, job, provider)
            if record.error_code == "doi_mismatch":
                record.error_code = None
                record.error_message = None
            continue
        mismatched.add(provider)
        record = _get_or_create_record(session, job, provider)
        record.error_code = "doi_mismatch"
        record.error_message = (
            f"provider DOI {fields.doi!r} does not match paper DOI {paper.doi!r}"
        )
    return mismatched


def _legacy_direct_merge(
    session, paper: Paper, parsed: dict[str, ParsedFields]
) -> dict[str, str]:
    """Legacy in-place merge used when the paper has no primary file.

    Only fills empty fields from the best eligible provider; existing values
    are never overwritten. The resolved map mirrors the paper's current state.
    """
    normalized_doi = paper.normalized_doi
    manual_confirmed: set[str] = set()
    resolved: dict[str, str] = {}

    title = best_field("title", parsed, normalized_doi, manual_confirmed)
    if title and not paper.title:
        paper.title = title[1]
        paper.normalized_title = title[1].lower().strip()
        resolved["title"] = f"doi_provider:{title[0]}"

    authors = best_field("authors", parsed, normalized_doi, manual_confirmed)
    if authors:
        existing = session.execute(
            select(PaperAuthor).where(PaperAuthor.paper_id == paper.id)
        ).scalars().all()
        if not existing:
            for order, author in enumerate(authors[1], start=1):
                session.add(PaperAuthor(
                    paper_id=paper.id,
                    author_order=order,
                    full_name=author.full_name,
                    normalized_name=author.full_name.lower().strip(),
                    affiliation=author.affiliation,
                    orcid=author.orcid,
                ))
            resolved["authors"] = f"doi_provider:{authors[0]}"

    year = best_field("publication_year", parsed, normalized_doi, manual_confirmed)
    if year and not paper.publication_year:
        paper.publication_year = int(year[1])
        resolved["publication_year"] = f"doi_provider:{year[0]}"

    venue = best_field("venue", parsed, normalized_doi, manual_confirmed)
    if venue and not paper.venue:
        paper.venue = venue[1]
        resolved["venue"] = f"doi_provider:{venue[0]}"

    abstract = best_field("abstract", parsed, normalized_doi, manual_confirmed)
    if abstract and not paper.abstract:
        paper.abstract = abstract[1]
        resolved["abstract"] = f"doi_provider:{abstract[0]}"

    if normalized_doi:
        resolved["doi"] = "local"

    session.flush()
    existing_authors = session.execute(
        select(PaperAuthor).where(PaperAuthor.paper_id == paper.id)
    ).scalars().all()
    if paper.title:
        resolved.setdefault("title", "existing")
    if existing_authors:
        resolved.setdefault("authors", "existing")
    if paper.publication_year:
        resolved.setdefault("publication_year", "existing")
    if paper.venue:
        resolved.setdefault("venue", "existing")
    if paper.abstract:
        resolved.setdefault("abstract", "existing")

    return resolved


def _source_label(candidate) -> str:
    return f"{candidate.source_type}:{candidate.source_location}"


def _paper_has_authors(session, paper_id: str) -> bool:
    return session.execute(
        select(PaperAuthor.id).where(PaperAuthor.paper_id == paper_id).limit(1)
    ).scalars().first() is not None


def _next_retry_at(attempt_count: int, retry_after: datetime | None) -> datetime:
    """Compute next_retry_at: Retry-After preferred, else exponential backoff.

    Fallback delays are exactly 30, 120, 600, and 3600 seconds with plus/minus
    10 percent jitter, indexed by the 1-based attempt count and capped at the
    last entry.
    """
    now = _utcnow()
    if retry_after is not None:
        return retry_after
    idx = min(attempt_count - 1, len(_BACKOFF_SECONDS) - 1)
    base = _BACKOFF_SECONDS[idx]
    return now + timedelta(seconds=base + _jitter(base))


def collect_provider_results(
    paper_id: str,
) -> EnrichmentJobResult | None:
    """Rebuild an EnrichmentJobResult from persisted records (for the API)."""
    with SessionLocal() as session:
        job = session.execute(
            select(DOIEnrichmentJob).where(DOIEnrichmentJob.paper_id == paper_id)
        ).scalar_one_or_none()
        if job is None:
            paper = session.get(Paper, paper_id)
            if paper is None:
                return None
            return EnrichmentJobResult(
                paper_id=paper_id,
                doi=paper.normalized_doi,
                status=derive_enrichment_status(paper, None),
            )
        providers = [
            ProviderResult(
                provider=r.provider,
                status=r.status,
                http_status=r.http_status,
                fetched_at=r.fetched_at,
                attempt_count=r.attempt_count,
                next_retry_at=_as_aware(r.next_retry_at),
                error_code=r.error_code,
                error_message=r.error_message,
                fields=_fields_from_raw(r.provider, r.raw_json) if r.raw_json else [],
            )
            for r in job.provider_results
        ]
        return EnrichmentJobResult(
            paper_id=paper_id,
            doi=job.doi,
            status=job.status,
            providers=providers,
        )
