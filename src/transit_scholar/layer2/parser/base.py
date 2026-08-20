"""Parser adapter abstraction and normalized item model (FR-003).

A ``ParserAdapter`` converts a parser's native output into TransitScholar's
normalized ``ParserItem`` list. Adapters record their name, version, config and
config hash; availability probing is explicit so heavy dependencies are never
imported at module load time.
"""

from __future__ import annotations

import abc
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

from transit_scholar.layer2.config import config_hash

#: Valid ``item_type`` values; mirrors the canonical ``block_type`` vocabulary.
ITEM_TYPES: frozenset[str] = frozenset(
    {
        "paragraph",
        "heading",
        "list",
        "table",
        "equation",
        "figure",
        "caption",
        "footnote",
        "reference",
        "other",
    }
)


@dataclass
class ParserItem:
    """A single normalized item extracted by a parser adapter.

    ``order`` is the parser-observed reading order (used only as a hint; the
    normalizer renumbers canonically). ``content`` carries type-specific
    payload (latex, cells, label, ...) without domain semantics.
    """

    item_id: str
    item_type: str
    text: str
    order: int
    page: int
    bbox: list[float] | None = None
    source_item_id: str | None = None
    level: int = 1
    font_size: float | None = None
    content: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ParserItem":
        return cls(
            item_id=str(data["item_id"]),
            item_type=str(data["item_type"]),
            text=str(data["text"]),
            order=int(data["order"]),
            page=int(data["page"]),
            bbox=data.get("bbox"),
            source_item_id=data.get("source_item_id"),
            level=int(data.get("level", 1)),
            font_size=data.get("font_size"),
            content=dict(data.get("content", {})),
        )


@dataclass(frozen=True)
class ParserInfo:
    """Identity + config of one parser adapter invocation."""

    name: str
    version: str
    config: dict[str, Any]
    config_hash: str


@dataclass
class ParserAvailability:
    """Result of ``availability()`` probing."""

    available: bool
    reason: str | None = None
    version: str | None = None


@dataclass
class ParserResult:
    """Outcome of one ``parse()`` call.

    ``status`` is ``ok`` for a usable item list or ``dependency_missing`` /
    ``parser_unavailable`` / ``error`` for structured failures.
    ``native_page_count`` is the parser's own page observation used by
    validation (page-count mismatch detection).
    """

    status: str
    items: list[ParserItem] = field(default_factory=list)
    info: ParserInfo | None = None
    page_count: int | None = None
    error_code: str | None = None
    error_message: str | None = None
    warnings: list[str] = field(default_factory=list)
    parser_quality: dict[str, Any] = field(default_factory=dict)


class ParserAdapter(abc.ABC):
    """Unified adapter interface implemented by every parser backend."""

    #: Stable parser name recorded in manifests (e.g. ``docling``).
    name: str = "base"

    @property
    @abc.abstractmethod
    def version(self) -> str:
        ...

    @property
    @abc.abstractmethod
    def config(self) -> dict[str, Any]:
        ...

    @property
    def config_hash(self) -> str:
        return config_hash(self.config)

    def availability(self) -> ParserAvailability:
        """Whether the parser dependency is usable in this environment."""
        return ParserAvailability(available=True, version=self.version)

    @abc.abstractmethod
    def parse(self, pdf_path: str) -> ParserResult:
        """Parse a PDF into normalized ``ParserItem`` records."""


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_PARSER_FACTORIES: dict[str, Callable[["Layer2Config"], ParserAdapter]] = {}


def register_adapter(name: str, factory: Callable[["Layer2Config"], ParserAdapter]) -> None:
    """Register a parser factory under a stable name (test seam + registry)."""
    _PARSER_FACTORIES[name] = factory


def get_adapter_factory(name: str) -> Callable[["Layer2Config"], ParserAdapter] | None:
    return _PARSER_FACTORIES.get(name)


def all_registered_names() -> list[str]:
    return sorted(_PARSER_FACTORIES)
