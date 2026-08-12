"""Abstract provider client.

Each concrete client knows how to build the request URL / headers for its
provider and how to parse the response into a ``ProviderFetchResult``. Real
HTTP is implemented in Phase 3; Phase 2 tests mock ``fetch``.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from transit_scholar.config import settings
from transit_scholar.doi_enrichment.result import ProviderFetchResult


def _utcnow() -> datetime:
    """Module-level clock seam; tests monkeypatch this to freeze time."""
    return datetime.now(timezone.utc)


def parse_retry_after(value: str | None) -> datetime | None:
    """Parse a Retry-After header into an absolute timezone-aware UTC time.

    Accepts either non-negative delta-seconds or an RFC 7231 HTTP-date
    (IMF-fixdate, RFC 850, or asctime). Returns ``None`` for absent, negative,
    or unparseable values so callers fall back to their own backoff schedule.
    """
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        try:
            parsed = parsedate_to_datetime(value)
        except (TypeError, ValueError, OverflowError):
            return None
        if parsed is None:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        else:
            parsed = parsed.astimezone(timezone.utc)
        return parsed
    if seconds < 0:
        return None
    try:
        return _utcnow() + timedelta(seconds=seconds)
    except (TypeError, ValueError, OverflowError):
        return None


_API_KEY_PARAM_RE = re.compile(r"api_key=[^&\s\"']+")
# Non-secret replacement used whenever a configured credential value must not
# be persisted or surfaced.
_REDACTED = "[REDACTED]"


def redact_secrets(text: str | None) -> str | None:
    """Remove configured API-key values and ``api_key=`` query parameters.

    Error messages may embed the failing request URL (including an OpenAlex
    ``api_key`` query parameter) or, defensively, a Semantic Scholar key that
    leaked into an exception reason. Both are replaced with ``[REDACTED]``.
    """
    if not text:
        return text
    redacted = text
    for key in (settings.openalex_api_key, settings.semantic_scholar_api_key):
        if key:
            redacted = redacted.replace(key, _REDACTED)
    return _API_KEY_PARAM_RE.sub(f"api_key={_REDACTED}", redacted)


class ProviderClient(ABC):
    """Query a single DOI metadata provider.

    ``name`` is the provider key (``crossref`` / ``openalex`` /
    ``semantic_scholar``). ``build_request`` returns the URL + safe (key-free)
    headers so the service can persist them before any network call.
    """

    name: str
    timeout_seconds: int
    max_attempts: int

    @abstractmethod
    def fetch(self, doi: str) -> ProviderFetchResult:
        """Issue a DOI query. Returns a structured result, never raises."""

    @abstractmethod
    def build_request(
        self, doi: str
    ) -> tuple[str, dict[str, str]]:
        """Return ``(url, safe_headers)`` with no API key material."""
