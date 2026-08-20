"""Parser resolution for a parse run (FR-007 / plan §4.4).

``resolve_parsers`` returns the ordered, availability-filtered adapter list the
pipeline will try: primary (docling) -> fallback (mineru) -> diagnostic
(pymupdf4llm) -> native (``pymupdf_native``, always available because ``fitz``
is a base dependency). Unavailable adapters are probed and recorded truthfully
as ``dependency_missing`` by the caller; they are never silently skipped.
"""

from __future__ import annotations

from transit_scholar.layer2.config import Layer2Config
from transit_scholar.layer2.parser.base import ParserAdapter, get_adapter_factory

#: Resolution priority. The plan adds ``pymupdf_native`` (fitz) after the three
#: design-document parsers as the offline real-PDF smoke path.
_PRIORITY = (
    "docling",
    "mineru",
    "pymupdf4llm",
    "pymupdf_native",
)


def resolve_parsers(config: Layer2Config) -> list[ParserAdapter]:
    """Return availability-filtered adapters in priority order.

    An explicit ``config.parser_override`` pins a single adapter (used by the
    offline real-PDF smoke so heavy installed docling/mineru pipelines are
    never pulled in automatically). The override adapter is returned
    regardless of the availability probe result so the caller can record the
    truthful failure instead of silently skipping it.
    """
    override = config.parser_override
    if override:
        factory = get_adapter_factory(override)
        if factory is None:
            return []
        return [factory(config)]

    adapters: list[ParserAdapter] = []
    seen: set[str] = set()
    for name in _PRIORITY:
        factory = get_adapter_factory(name)
        if factory is None:
            continue
        adapter = factory(config)
        if adapter.name in seen:
            continue
        seen.add(adapter.name)
        availability = adapter.availability()
        if availability.available:
            adapters.append(adapter)
    return adapters


def probe_all(config: Layer2Config) -> list[dict[str, object]]:
    """Probe every registered parser and report availability (for smoke).

    Returns one record per registered adapter with name, available, version and
    a structured reason when unavailable.
    """
    records: list[dict[str, object]] = []
    for name in sorted(set(_PRIORITY) | {"fake"}):
        factory = get_adapter_factory(name)
        if factory is None:
            records.append(
                {
                    "name": name,
                    "available": False,
                    "reason": "unregistered",
                }
            )
            continue
        adapter = factory(config)
        availability = adapter.availability()
        records.append(
            {
                "name": adapter.name,
                "available": availability.available,
                "version": availability.version,
                "reason": availability.reason,
            }
        )
    return records
