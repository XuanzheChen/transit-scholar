"""Rate limiting for provider HTTP traffic.

A SerialRateLimiter enforces a per-provider minimum interval between requests.
For this single-user local tool max_concurrency is effectively 1 (serial), so
the limiter only tracks ``next_available_at`` per provider. The limiter is
mostly exercised indirectly in tests via ``release(..., retry_after=...)``.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class RateLimiter(ABC):
    """Block until a provider may issue a request, then record its release."""

    @abstractmethod
    def acquire(self, provider: str) -> None:
        """Block until the provider is allowed to send a request."""

    @abstractmethod
    def release(
        self, provider: str, *, retry_after: datetime | None = None
    ) -> None:
        """Release a request slot and advance ``next_available_at``."""

    @abstractmethod
    def next_available_at(self, provider: str) -> datetime | None:
        """Return when the provider next becomes available (may be past)."""


class SerialRateLimiter(RateLimiter):
    """Per-provider serial throttle keyed on ``next_available_at``."""

    def __init__(self, min_interval_ms: dict[str, int]) -> None:
        self._min_interval_ms = dict(min_interval_ms)
        self._next_available_at: dict[str, datetime] = {}

    def acquire(self, provider: str) -> None:
        now = _utcnow()
        target = self._next_available_at.get(provider)
        if target is None:
            return
        delay = (target - now).total_seconds()
        if delay > 0:
            time.sleep(delay)

    def release(
        self, provider: str, *, retry_after: datetime | None = None
    ) -> None:
        now = _utcnow()
        interval_ms = self._min_interval_ms.get(provider, 0)
        base = now + timedelta(milliseconds=interval_ms)
        if retry_after is not None:
            base = max(base, retry_after)
        self._next_available_at[provider] = base

    def next_available_at(self, provider: str) -> datetime | None:
        return self._next_available_at.get(provider)
