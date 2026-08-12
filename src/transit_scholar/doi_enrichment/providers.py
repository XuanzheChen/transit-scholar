"""Provider registry and per-provider configuration.

The registry returns the cascade-ordered list of clients that should run for a
paper. OpenAlex is omitted when no API key is configured; the service layer
writes a ``skipped``/``missing_api_key`` record for it so the UI can show the
reason.
"""

from __future__ import annotations

from transit_scholar.config import settings
from transit_scholar.doi_enrichment.clients import (
    CrossrefClient,
    OpenAlexClient,
    ProviderClient,
    SemanticScholarClient,
)
from transit_scholar.doi_enrichment.rate_limiter import SerialRateLimiter

# Per-provider throttle / retry defaults (Phase 1 spec §4.3).
PROVIDER_MIN_INTERVAL_MS: dict[str, int] = {
    "crossref": 250,
    "openalex": 500,
    "semantic_scholar": 1100,
}
PROVIDER_MAX_CONCURRENCY: dict[str, int] = {
    "crossref": 1,
    "openalex": 1,
    "semantic_scholar": 1,
}
PROVIDER_TIMEOUT_SECONDS: dict[str, int] = {
    "crossref": 15,
    "openalex": 15,
    "semantic_scholar": 20,
}
PROVIDER_MAX_ATTEMPTS: dict[str, int] = {
    "crossref": 4,
    "openalex": 4,
    "semantic_scholar": 4,
}

# Cascade order: crossref -> openalex (if key) -> semantic_scholar.
CASCADE_ORDER = ("crossref", "openalex", "semantic_scholar")


class ProviderRegistry:
    """Builds the ordered, key-gated list of provider clients."""

    def __init__(self, clients: dict[str, ProviderClient] | None = None) -> None:
        self._clients = clients

    def _build(self) -> dict[str, ProviderClient]:
        if self._clients is not None:
            return self._clients
        return {
            "crossref": CrossrefClient(),
            "openalex": OpenAlexClient(),
            "semantic_scholar": SemanticScholarClient(),
        }

    def ordered_clients(self) -> list[ProviderClient]:
        """Return clients in cascade order, skipping OpenAlex when keyless."""
        clients = self._build()
        result: list[ProviderClient] = []
        for name in CASCADE_ORDER:
            if name == "openalex" and not settings.openalex_api_key:
                continue
            result.append(clients[name])
        return result

    def client_for(self, provider: str) -> ProviderClient:
        return self._build()[provider]


def default_rate_limiter() -> SerialRateLimiter:
    return SerialRateLimiter(PROVIDER_MIN_INTERVAL_MS)
