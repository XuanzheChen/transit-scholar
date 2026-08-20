"""Deterministic fake parser adapter for offline tests and eval fixtures.

Produces a fully configurable, deterministic ``ParserItem`` stream so tests can
exercise the canonical normalizer, renderer, chunker and retrieval layers
without any heavy parser dependency. Never used by the production pipeline.
"""

from __future__ import annotations

from typing import Any

from transit_scholar.layer2.config import Layer2Config
from transit_scholar.layer2.parser.base import (
    ParserAdapter,
    ParserAvailability,
    ParserInfo,
    ParserItem,
    ParserResult,
    register_adapter,
)

FAKE_PARSER_VERSION = "1.0.0"


class FakeParserAdapter(ParserAdapter):
    """Deterministic fake producing a fixed ``ParserItem`` list."""

    name = "fake"

    def __init__(
        self,
        config: Layer2Config | None = None,
        *,
        items: list[ParserItem] | None = None,
        page_count: int | None = None,
        status: str = "ok",
        error_code: str | None = None,
        error_message: str | None = None,
        parser_quality: dict[str, Any] | None = None,
        version: str = FAKE_PARSER_VERSION,
    ) -> None:
        self._items = items or []
        self._page_count = page_count
        self._status = status
        self._error_code = error_code
        self._error_message = error_message
        self._parser_quality = parser_quality or {}
        self._version = version

    @property
    def version(self) -> str:
        return self._version

    @property
    def config(self) -> dict[str, Any]:
        return {
            "type": "fake",
            "item_count": len(self._items),
            "page_count": self._page_count,
        }

    def availability(self) -> ParserAvailability:
        return ParserAvailability(available=True, version=self.version)

    def parse(self, pdf_path: str) -> ParserResult:
        info = ParserInfo(
            name=self.name,
            version=self.version,
            config=self.config,
            config_hash=self.config_hash,
        )
        return ParserResult(
            status=self._status,
            items=list(self._items),
            info=info,
            page_count=self._page_count,
            error_code=self._error_code,
            error_message=self._error_message,
            parser_quality=dict(self._parser_quality),
        )


def make_item(
    *,
    item_id: str,
    item_type: str,
    text: str,
    order: int,
    page: int,
    bbox: list[float] | None = None,
    level: int = 1,
    font_size: float | None = None,
    source_item_id: str | None = None,
    content: dict[str, Any] | None = None,
) -> ParserItem:
    return ParserItem(
        item_id=item_id,
        item_type=item_type,
        text=text,
        order=order,
        page=page,
        bbox=bbox,
        level=level,
        font_size=font_size,
        source_item_id=source_item_id or item_id,
        content=content or {},
    )


def _factory(config: Layer2Config) -> FakeParserAdapter:
    return FakeParserAdapter(config)


register_adapter("fake", _factory)
