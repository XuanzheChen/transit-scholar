"""Crossref DOI client.

Uses the REST API ``/works/{doi}``. When ``mailto`` is configured it is added
as a query param and into the User-Agent so the request enters the polite
pool. The mailto value is non-sensitive and is allowed to appear in persisted
records; no API key is involved.
"""

from __future__ import annotations

from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

from transit_scholar.config import settings
from transit_scholar.doi_enrichment.clients.base import (
    ProviderClient,
    parse_retry_after,
    redact_secrets,
)
from transit_scholar.doi_enrichment.result import (
    PROVIDER_FETCHED,
    PROVIDER_NOT_FOUND,
    ProviderFetchResult,
)

_BASE_URL = "https://api.crossref.org/works/"
_USER_AGENT = "TransitScholar (https://example.com; mailto:{mailto})"


class CrossrefClient(ProviderClient):
    name = "crossref"
    timeout_seconds = 15
    max_attempts = 4

    def build_request(self, doi: str) -> tuple[str, dict[str, str]]:
        mailto = settings.crossref_mailto
        url = _BASE_URL + quote(doi, safe="/")
        if mailto:
            url = url + f"?mailto={quote(mailto, safe='@')}"
        headers = {"Accept": "application/json"}
        if mailto:
            headers["User-Agent"] = _USER_AGENT.format(mailto=mailto)
        else:
            headers["User-Agent"] = "TransitScholar (https://example.com)"
        return url, headers

    def fetch(self, doi: str) -> ProviderFetchResult:
        url, headers = self.build_request(doi)
        request = Request(url, headers=headers)
        try:
            with urlopen(request, timeout=self.timeout_seconds) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
        except HTTPError as exc:
            # HTTPError carries the response headers, so a Retry-After on a
            # 429/5xx response is still observable by the service layer.
            retry_after = parse_retry_after(
                exc.headers.get("Retry-After") if exc.headers else None
            )
            if exc.code == 404:
                return ProviderFetchResult(
                    provider=self.name,
                    status=PROVIDER_NOT_FOUND,
                    http_status=404,
                    error_code="not_found",
                    retry_after=retry_after,
                )
            return ProviderFetchResult(
                provider=self.name,
                status="__network_error__",
                http_status=exc.code,
                error_code=None,
                error_message=redact_secrets(str(exc)),
                retry_after=retry_after,
            )
        except Exception as exc:  # noqa: BLE001 - mapped to result by caller
            status = getattr(exc, "code", None)
            return ProviderFetchResult(
                provider=self.name,
                status="__network_error__",
                http_status=status,
                error_code="network_error",
                error_message=redact_secrets(str(exc)),
            )
        return ProviderFetchResult(
            provider=self.name,
            status=PROVIDER_FETCHED,
            http_status=resp.status,
            raw_json=raw,
            retry_after=parse_retry_after(resp.headers.get("Retry-After")),
        )
