"""Semantic Scholar DOI client.

Resolves a DOI via the ``/paper/DOI:{doi}`` endpoint. The API key is optional;
when present it is sent in the ``x-api-key`` header but stripped from the
persisted ``request_headers_json`` and redacted from any error message so no
key material is stored.
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

_BASE_URL = "https://api.semanticscholar.org/graph/v1/paper/DOI:"
_KEY_HEADER = "x-api-key"


def _safe_headers(headers: dict[str, str]) -> dict[str, str]:
    """Return a copy of headers with the API-key header removed."""
    return {k: v for k, v in headers.items() if k.lower() != _KEY_HEADER}


class SemanticScholarClient(ProviderClient):
    name = "semantic_scholar"
    timeout_seconds = 20
    max_attempts = 4

    def build_request(self, doi: str) -> tuple[str, dict[str, str]]:
        url = _BASE_URL + quote(doi, safe="/:")
        headers = {"Accept": "application/json"}
        if settings.semantic_scholar_api_key:
            headers[_KEY_HEADER] = settings.semantic_scholar_api_key
        return url, _safe_headers(headers)

    def fetch(self, doi: str) -> ProviderFetchResult:
        url = _BASE_URL + quote(doi, safe="/:")
        headers = {"Accept": "application/json"}
        if settings.semantic_scholar_api_key:
            headers[_KEY_HEADER] = settings.semantic_scholar_api_key
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
