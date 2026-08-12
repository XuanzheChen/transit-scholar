"""OpenAlex DOI client.

Resolves a DOI via the ``/works/doi:{doi}`` endpoint. OpenAlex accepts an
optional ``api_key`` query param for higher rate limits; when present the key
is stripped from the persisted ``request_url`` and redacted from any error
message so no key material is stored.
"""

from __future__ import annotations

from urllib.error import HTTPError
from urllib.parse import parse_qs, quote, urlencode, urlsplit, urlunsplit
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

_BASE_URL = "https://api.openalex.org/works/doi:"
_KEY_PARAM = "api_key"


def _strip_key_from_url(url: str) -> str:
    """Remove the API-key query parameter from a URL before persistence."""
    parts = urlsplit(url)
    params = parse_qs(parts.query, keep_blank_values=True)
    params.pop(_KEY_PARAM, None)
    cleaned = urlencode(params, doseq=True)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, cleaned, parts.fragment))


class OpenAlexClient(ProviderClient):
    name = "openalex"
    timeout_seconds = 15
    max_attempts = 4

    def build_request(self, doi: str) -> tuple[str, dict[str, str]]:
        url = _BASE_URL + quote(doi, safe="/:")
        if settings.openalex_api_key:
            url = url + f"?api_key={quote(settings.openalex_api_key, safe='')}"
        return url, {"Accept": "application/json"}

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


def sanitize_url(url: str) -> str:
    """Expose key stripping for the service layer / tests."""
    return _strip_key_from_url(url)
