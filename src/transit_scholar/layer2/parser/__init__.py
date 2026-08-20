"""Parser adapter package for Layer2 Step1.

Downstream code (normalizer, renderer, chunker, retrieval) only ever consumes
the normalized ``ParserItem`` / ``ParserResult`` structures from
``transit_scholar.layer2.parser.base``; it never sees Docling / MinerU /
PyMuPDF4LLM native objects. Heavy adapters import their dependencies lazily and
report ``dependency_missing`` / ``parser_unavailable`` instead of fabricating a
successful parse.
"""

from __future__ import annotations

from transit_scholar.layer2.parser.base import (
    ParserAdapter,
    ParserAvailability,
    ParserInfo,
    ParserItem,
    ParserResult,
    register_adapter,
)
from transit_scholar.layer2.parser.registry import resolve_parsers

__all__ = [
    "ParserAdapter",
    "ParserAvailability",
    "ParserInfo",
    "ParserItem",
    "ParserResult",
    "register_adapter",
    "resolve_parsers",
]


def _ensure_registry() -> None:
    """Import adapter modules so they register themselves (idempotent)."""
    from transit_scholar.layer2.parser import (  # noqa: F401
        docling,
        fake,
        fitz_native,
        mineru,
        pymupdf4llm,
    )


_ensure_registry()
