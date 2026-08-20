"""Deterministic evidence integrity validation for L2S2 Package C
(FR-C-003 / AC-C-03).

Every ``EvidenceRef`` of a ``SchemaInstance`` is checked against canonical
block data provided by an injectable reader:

    reader(paper_id, block_ids) -> dict[block_id, block_data]
    reader(paper_id, block_ids) -> list[block_data]

``block_data`` follows the ``CanonicalBlock.to_dict()`` shape of the L2S1
canonical layer (``block_id``/``paper_id``/``block_type``/``section_id``/
``order``/``text``/``pages``/``provenance``/...). Tests always use fake
dictionaries; no real parse run, PDF, or retrieval index is ever touched.

Checks per evidence ref, in fixed deterministic order:

1. block exists                      -> ``evidence_block_missing`` (error)
2. block paper_id matches instance   -> ``evidence_paper_mismatch`` (error)
3. char range inside block text      -> ``evidence_char_range_invalid`` (error)
4. quote/span consistency            -> ``evidence_quote_mismatch`` (error);
   empty quotes are skipped; invalid ranges skip the quote check; Package B
   may bind the whole retrieval hit text as quote, so either side may contain
   the other
5. pages traceable from block        -> ``evidence_pages_not_traceable`` (warning)
6. section_path matches comparable canonical path/title data
                                      -> ``evidence_section_mismatch`` (error)
   canonical section data missing
                                      -> ``evidence_section_unverifiable`` (warning)

A reader failure (exception, ``None``, or malformed result) produces a
single ``canonical_read_failed`` error: a system failure is never silenced
and never misreported as ``not_found``.
"""

from __future__ import annotations

from typing import Any, Callable

from .models import SchemaInstance, ValidationIssue

#: Injectable canonical block reader. The dict form is convenient for tests;
#: the list form matches L2S1 ``read_blocks()``.
CanonicalBlocks = dict[str, dict[str, Any]] | list[dict[str, Any]]
CanonicalReader = Callable[[str, list[str]], CanonicalBlocks]


class CanonicalReadError(Exception):
    """Canonical layer could not be read (system failure, never not_found)."""

    def __init__(self, message: str = ""):
        super().__init__(message or "canonical read failed")
        self.error_code = "canonical_read_failed"


def _canonical_read_failed_issue(message: str) -> ValidationIssue:
    return ValidationIssue(
        type="canonical_read_failed",
        severity="error",
        message=message,
    )


def validate_evidence_integrity(
    instance: SchemaInstance,
    reader: CanonicalReader | None,
    *,
    paper_id: str | None = None,
) -> list[ValidationIssue]:
    """Validate every evidence ref of ``instance`` against canonical blocks.

    Deterministic: no randomness, no network, no time dependency. The
    ``paper_id`` override wins over ``instance.paper_id`` for comparison.
    """
    effective_paper_id = paper_id or instance.paper_id
    block_map, read_error = _read_canonical_blocks(
        instance, reader, effective_paper_id
    )
    if read_error is not None:
        return [_canonical_read_failed_issue(read_error)]

    issues: list[ValidationIssue] = []
    for field_id, result in instance.fields.items():
        for ref in result.evidence:
            issues.extend(
                _validate_one_ref(
                    field_id=field_id,
                    block_id=ref.block_id,
                    char_start=ref.char_start,
                    char_end=ref.char_end,
                    quote=ref.quote,
                    pages=list(ref.pages),
                    section_path=list(ref.section_path),
                    block_map=block_map,
                    paper_id=effective_paper_id,
                )
            )
    return issues


def _read_canonical_blocks(
    instance: SchemaInstance,
    reader: CanonicalReader | None,
    paper_id: str,
) -> tuple[dict[str, dict[str, Any]] | None, str | None]:
    if reader is None:
        return None, (
            "canonical reader is unavailable; evidence integrity could not "
            "be verified"
        )
    seen: list[str] = []
    for result in instance.fields.values():
        for ref in result.evidence:
            if ref.block_id not in seen:
                seen.append(ref.block_id)
    try:
        blocks = reader(paper_id, seen)
    except Exception as exc:  # noqa: BLE001 - explicit system failure
        return None, (
            f"canonical reader raised {type(exc).__name__}: {exc}"
        )
    block_map, normalize_error = _normalize_canonical_blocks(blocks)
    if normalize_error is not None:
        return None, (
            f"canonical reader returned malformed canonical blocks: "
            f"{normalize_error}"
        )
    return block_map, None


def _normalize_canonical_blocks(
    blocks: CanonicalBlocks | Any,
) -> tuple[dict[str, dict[str, Any]] | None, str | None]:
    if isinstance(blocks, dict):
        return blocks, None
    if isinstance(blocks, list):
        block_map: dict[str, dict[str, Any]] = {}
        for index, block in enumerate(blocks):
            if not isinstance(block, dict):
                return None, f"item #{index} is {type(block).__name__!r}, not a mapping"
            block_id = block.get("block_id")
            if not isinstance(block_id, str) or not block_id:
                return None, f"item #{index} has no string block_id"
            block_map[block_id] = block
        return block_map, None
    return None, f"expected dict or list, got {type(blocks).__name__!r}"


def _validate_one_ref(
    *,
    field_id: str,
    block_id: str,
    char_start: int,
    char_end: int,
    quote: str,
    pages: list[int],
    section_path: list[str],
    block_map: dict[str, dict[str, Any]],
    paper_id: str,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []

    block = block_map.get(block_id)
    if block is None:
        issues.append(
            ValidationIssue(
                type="evidence_block_missing",
                severity="error",
                message=(
                    f"field {field_id!r} evidence block {block_id!r} does not "
                    f"exist in canonical data"
                ),
                fields=[field_id],
            )
        )
        return issues

    if not isinstance(block, dict):
        issues.append(
            _canonical_read_failed_issue(
                f"canonical block {block_id!r} is malformed (not a mapping)"
            )
        )
        return issues

    if block.get("paper_id") != paper_id:
        issues.append(
            ValidationIssue(
                type="evidence_paper_mismatch",
                severity="error",
                message=(
                    f"field {field_id!r} evidence block {block_id!r} belongs "
                    f"to paper {block.get('paper_id')!r}, expected {paper_id!r}"
                ),
                fields=[field_id],
            )
        )

    text = block.get("text")
    text = text if isinstance(text, str) else ""

    range_invalid = (
        char_start < 0
        or char_end < char_start
        or char_start > len(text)
        or char_end > len(text)
    )
    if range_invalid:
        issues.append(
            ValidationIssue(
                type="evidence_char_range_invalid",
                severity="error",
                message=(
                    f"field {field_id!r} evidence block {block_id!r} char "
                    f"range [{char_start}, {char_end}) is invalid for text of "
                    f"length {len(text)}"
                ),
                fields=[field_id],
            )
        )

    if not range_invalid and quote:
        substring = text[char_start:char_end]
        if not _quote_matches_span(quote, substring):
            issues.append(
                ValidationIssue(
                    type="evidence_quote_mismatch",
                    severity="error",
                    message=(
                        f"field {field_id!r} evidence block {block_id!r} "
                        f"quote {quote!r} does not match canonical substring "
                        f"{substring!r}"
                    ),
                    fields=[field_id],
                )
            )

    if pages:
        block_pages = block.get("pages")
        block_pages = block_pages if isinstance(block_pages, list) else []
        provenance = block.get("provenance")
        provenance = provenance if isinstance(provenance, list) else []
        traceable_pages = {int(p) for p in block_pages if isinstance(p, int)}
        for item in provenance:
            if isinstance(item, dict) and item.get("page") is not None:
                traceable_pages.add(int(item["page"]))
        untraceable = [p for p in pages if p not in traceable_pages]
        if untraceable:
            issues.append(
                ValidationIssue(
                    type="evidence_pages_not_traceable",
                    severity="warning",
                    message=(
                        f"field {field_id!r} evidence block {block_id!r} "
                        f"pages {untraceable} cannot be traced back to the "
                        f"canonical block"
                    ),
                    fields=[field_id],
                )
            )

    if section_path:
        comparable_section_path = _canonical_section_path(block)
        section_id = block.get("section_id")
        if comparable_section_path is not None:
            if comparable_section_path != section_path:
                issues.append(
                    ValidationIssue(
                        type="evidence_section_mismatch",
                        severity="error",
                        message=(
                            f"field {field_id!r} evidence block {block_id!r} "
                            f"section_path {section_path!r} does not match "
                            f"canonical section_path {comparable_section_path!r}"
                        ),
                        fields=[field_id],
                    )
                )
        elif section_id is None:
            issues.append(
                ValidationIssue(
                    type="evidence_section_unverifiable",
                    severity="warning",
                    message=(
                        f"field {field_id!r} evidence block {block_id!r} has "
                        f"no canonical section data; section_path "
                        f"{section_path!r} cannot be verified"
                    ),
                    fields=[field_id],
                )
            )
        else:
            # L2S1 public read_blocks() exposes stable internal section IDs
            # (for example sec_001), while RetrievalHit.section_path stores
            # human title paths. Without a comparable title path, there is no
            # deterministic equality check to perform.
            pass

    return issues


def _quote_matches_span(quote: str, substring: str) -> bool:
    if quote == substring:
        return True
    if quote in substring:
        return True
    return bool(substring and substring in quote)


def _canonical_section_path(block: dict[str, Any]) -> list[str] | None:
    value = block.get("section_path")
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return list(value)
    content = block.get("content")
    if isinstance(content, dict):
        value = content.get("section_path")
        if isinstance(value, list) and all(isinstance(item, str) for item in value):
            return list(value)
    relations = block.get("relations")
    if isinstance(relations, dict):
        value = relations.get("section_path")
        if isinstance(value, list) and all(isinstance(item, str) for item in value):
            return list(value)
    return None
