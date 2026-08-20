"""Per-unit parser quality metrics and record field validation (FR-PARSER).

These heuristics describe *structure* (page/block/type counts, provenance
coverage, caption relation completeness) and deterministic *noise* signals
(replacement chars, duplicates, meaningful pages). They never substitute for
the human quality review that ``benchmark.review`` materializes.
"""

from __future__ import annotations

from typing import Any

from transit_scholar.layer2.config import Layer2Config
from transit_scholar.layer2.parser.base import ParserResult
from transit_scholar.layer2.schema import (
    CanonicalBlock,
    CanonicalDocument,
    CanonicalSection,
)
from transit_scholar.layer2.validation import ParseValidation, ParseValidator

#: Allowed per-unit status vocabulary.
UNIT_STATUSES = (
    "passed",
    "degraded",
    "failed",
    "timeout",
    "dependency_missing",
    "error",
)


def compute_unit_quality(
    parser_result: ParserResult,
    document: CanonicalDocument | None,
    sections: list[CanonicalSection],
    blocks: list[CanonicalBlock],
    validation: ParseValidation | None,
    page_count: int | None,
    config: Layer2Config,
) -> dict[str, Any]:
    """Compute the machine-checkable quality fields of one (paper, parser)."""
    page_count = page_count or (document.page_count if document else None) or 0
    type_counts: dict[str, int] = {}
    for block in blocks:
        type_counts[block.block_type] = type_counts.get(block.block_type, 0) + 1

    total_chars = sum(len(b.text) for b in blocks)
    replacement_chars = sum(b.text.count("\ufffd") for b in blocks)
    replacement_ratio = replacement_chars / total_chars if total_chars else 0.0

    provenance_segments = [
        prov for block in blocks for prov in block.provenance
    ]
    valid_page_segments = [
        p for p in provenance_segments if page_count <= 0 or 1 <= p.page <= page_count
    ]
    bbox_segments = [p for p in provenance_segments if p.bbox]
    provenance_page_coverage = (
        len(valid_page_segments) / len(provenance_segments)
        if provenance_segments
        else 0.0
    )
    bbox_coverage = (
        len(bbox_segments) / len(provenance_segments)
        if provenance_segments
        else 0.0
    )

    captions = [b for b in blocks if b.block_type == "caption"]
    bound_caption_ids: set[str] = set()
    for block in blocks:
        for caption_id in block.relations.get("caption_block_ids", []) or []:
            bound_caption_ids.add(str(caption_id))
    caption_relation_completeness = (
        len(bound_caption_ids) / len(captions) if captions else 0.0
    )

    validator = ParseValidator(config)
    meaningful_ratio = validator._meaningful_text_page_ratio(blocks, page_count)
    duplicate_ratio = validator._suspicious_duplicate_ratio(blocks)

    return {
        "page_count": page_count,
        "section_count": len(sections),
        "block_count": len(blocks),
        "type_counts": type_counts,
        # ``None`` (honest "unknown") when a body exists without page
        # provenance; never 0.0 so an existing body is not misjudged.
        "meaningful_page_ratio": (
            round(meaningful_ratio, 6) if meaningful_ratio is not None else None
        ),
        "replacement_char_ratio": round(replacement_ratio, 6),
        "duplicate_ratio": round(duplicate_ratio, 6),
        "provenance_page_coverage": round(provenance_page_coverage, 6),
        "bbox_coverage": round(bbox_coverage, 6),
        "table_count": type_counts.get("table", 0),
        "figure_count": type_counts.get("figure", 0),
        "equation_count": type_counts.get("equation", 0),
        "caption_count": len(captions),
        "caption_relation_completeness": round(caption_relation_completeness, 6),
        "validation_status": validation.status if validation else None,
        "validation_signals": [s.to_dict() for s in validation.signals] if validation else [],
    }


# ---------------------------------------------------------------------------
# Record field validation (AC-PARSER-004)
# ---------------------------------------------------------------------------

_REQUIRED_FIELDS = (
    "pdf_name",
    "pdf_sha256",
    "parser_name",
    "parser_version",
    "parser_config_hash",
    "status",
    "runtime_s",
    "page_count",
    "section_count",
    "block_count",
    "type_counts",
    "meaningful_page_ratio",
    "replacement_char_ratio",
    "duplicate_ratio",
    "provenance_page_coverage",
    "bbox_coverage",
    "table_count",
    "figure_count",
    "equation_count",
    "caption_count",
    "caption_relation_completeness",
    "artifact_dir",
)

#: Stable ``None`` defaults for quality metrics on failure records: error /
#: timeout / dependency_missing records still satisfy the required schema;
#: unavailable metrics are ``null`` (never a missing field).
FAILURE_QUALITY_NULLS: dict[str, Any] = {
    "page_count": None,
    "section_count": None,
    "block_count": None,
    "type_counts": None,
    "meaningful_page_ratio": None,
    "replacement_char_ratio": None,
    "duplicate_ratio": None,
    "provenance_page_coverage": None,
    "bbox_coverage": None,
    "table_count": None,
    "figure_count": None,
    "equation_count": None,
    "caption_count": None,
    "caption_relation_completeness": None,
}


def complete_failure_record(record: dict[str, Any]) -> dict[str, Any]:
    """Fill every required quality field of a failure record with ``None``.

    ``error`` / ``timeout`` / ``dependency_missing`` records must satisfy the
    stable unit-record schema so the parent never prints a chain of
    ``missing required field`` violations for legitimate failures. Quality
    metrics that are genuinely unavailable stay ``null``.
    """
    completed = dict(record)
    for field, value in FAILURE_QUALITY_NULLS.items():
        completed.setdefault(field, value)
    completed.setdefault("warnings", [])
    return completed


def validate_unit_record(record: dict[str, Any]) -> list[str]:
    """Return a list of structural violations for a per-unit record.

    Every error is a string so callers can surface them directly; an empty
    list means the record satisfies the required field/type/range contract.
    """
    errors: list[str] = []
    for field in _REQUIRED_FIELDS:
        if field not in record:
            errors.append(f"missing required field {field!r}")

    status = record.get("status")
    if status is not None and status not in UNIT_STATUSES:
        errors.append(f"illegal status {status!r}")

    for field in ("page_count", "section_count", "block_count"):
        value = record.get(field)
        if value is not None and (not isinstance(value, int) or value < 0):
            errors.append(f"{field} must be a non-negative integer")

    for field in ("runtime_s",):
        value = record.get(field)
        if value is not None and (not isinstance(value, (int, float)) or value < 0):
            errors.append(f"{field} must be a non-negative number")

    for field in (
        "meaningful_page_ratio",
        "replacement_char_ratio",
        "duplicate_ratio",
        "provenance_page_coverage",
        "bbox_coverage",
        "caption_relation_completeness",
    ):
        value = record.get(field)
        if value is not None and (not isinstance(value, (int, float)) or not 0.0 <= value <= 1.0):
            errors.append(f"{field} must be in [0, 1]")

    type_counts = record.get("type_counts")
    if type_counts is not None and not isinstance(type_counts, dict):
        errors.append("type_counts must be a dict")

    return errors
