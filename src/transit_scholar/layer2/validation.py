"""Parse validation and quality signals (FR-007).

Emits exactly one pipeline-level status -- ``passed`` / ``degraded`` /
``failed`` -- plus explainable signals. Thresholds are config-driven
(AC-L2S1-CONFIG-02). Hard failures make the result unusable; degraded signals
never discard the result but trigger the whole-document fallback in the
pipeline.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from transit_scholar.layer2.config import Layer2Config
from transit_scholar.layer2.parser.base import ParserResult
from transit_scholar.layer2.schema import (
    CanonicalBlock,
    CanonicalDocument,
    CanonicalSection,
)

_STRUCTURE_HINT_RE = re.compile(r"\b(?:Table|Tab\.?|Figure|Fig\.?|Eq\.?)\s*\d+", re.IGNORECASE)
_STRUCUTRAL_HINT_RE = _STRUCTURE_HINT_RE


@dataclass
class ValidationSignal:
    name: str
    ok: bool
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "ok": self.ok, "message": self.message}


@dataclass
class ParseValidation:
    status: str  # passed / degraded / failed
    signals: list[ValidationSignal] = field(default_factory=list)
    error_code: str | None = None
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "signals": [s.to_dict() for s in self.signals],
            "error_code": self.error_code,
            "warnings": list(self.warnings),
        }


class ParseValidator:
    """Deterministic validation over a canonical parse."""

    def __init__(self, config: Layer2Config) -> None:
        self._config = config

    def validate(
        self,
        parser_result: ParserResult,
        document: CanonicalDocument | None,
        sections: list[CanonicalSection],
        blocks: list[CanonicalBlock],
    ) -> ParseValidation:
        signals: list[ValidationSignal] = []
        warnings: list[str] = []

        # --- hard failure: parser exception / missing dependency ------------
        if parser_result.status in ("error", "dependency_missing", "parser_unavailable"):
            return ParseValidation(
                status="failed",
                signals=[
                    ValidationSignal(
                        "parser_status", False,
                        f"parser reported {parser_result.status}",
                    )
                ],
                error_code=parser_result.error_code or "PARSER_FAILED",
                warnings=warnings,
            )

        # --- hard failure: no canonical document ----------------------------
        if document is None:
            return ParseValidation(
                status="failed",
                signals=[
                    ValidationSignal("canonical_document", False, "no canonical document")
                ],
                error_code="NO_CANONICAL_DOCUMENT",
                warnings=warnings,
            )

        # --- hard failure: page-count mismatch ------------------------------
        page_count = document.page_count
        if parser_result.page_count and page_count != parser_result.page_count:
            signals.append(
                ValidationSignal(
                    "page_count",
                    False,
                    f"page_count mismatch: source={parser_result.page_count} "
                    f"canonical={page_count}",
                )
            )
        else:
            signals.append(ValidationSignal("page_count", True, "page_count consistent"))

        # --- hard failure: near-empty body ----------------------------------
        # ``meaningful_ratio`` is ``None`` when the body exists but no block
        # carries page-level provenance: page coverage is unknown, so an
        # existing body must NOT be misjudged as near-empty.
        total_chars = sum(len(b.text) for b in blocks)
        meaningful_ratio = self._meaningful_text_page_ratio(
            blocks, page_count
        )
        if not blocks or (
            meaningful_ratio is not None
            and meaningful_ratio < self._config.failed_if_meaningful_text_page_ratio_below
        ):
            ratio_text = (
                f"meaningful_text_page_ratio={meaningful_ratio:.3f} "
                if meaningful_ratio is not None
                else "meaningful_text_page_ratio=unknown (no page provenance) "
            )
            signals.append(
                ValidationSignal(
                    "near_empty_body",
                    False,
                    f"near-empty body: {ratio_text}total_chars={total_chars}",
                )
            )
        else:
            signals.append(
                ValidationSignal(
                    "near_empty_body",
                    True,
                    "body text present"
                    if meaningful_ratio is not None
                    else "body text present (page coverage unknown)",
                )
            )

        # --- hard failure: reading order unbuildable -------------------------
        order_values = [b.order for b in blocks]
        order_ok = len(set(order_values)) == len(order_values) and order_values == sorted(
            order_values
        )
        if not order_ok:
            signals.append(
                ValidationSignal(
                    "reading_order", False, "canonical reading order is not monotonic"
                )
            )
        else:
            signals.append(ValidationSignal("reading_order", True, "reading order monotonic"))

        # --- hard failure: illegal provenance page ---------------------------
        provenance_ok = True
        for block in blocks:
            for prov in block.provenance:
                if prov.page < 1 or (page_count and prov.page > page_count):
                    provenance_ok = False
        signals.append(
            ValidationSignal(
                "provenance_legal",
                provenance_ok,
                "provenance pages in range" if provenance_ok else "illegal provenance page",
            )
        )

        # --- hard failure: corrupted canonical structure ----------------------
        structure_ok = True
        for block in blocks:
            if not block.block_id or block.block_type not in _ALLOWED_SET:
                structure_ok = False
        section_heading_ids = {s.heading_block_id for s in sections}
        block_ids = {b.block_id for b in blocks}
        for sec in sections:
            if sec.heading_block_id not in block_ids:
                structure_ok = False
        for sec in sections:
            if sec.parent_section_id is not None and sec.parent_section_id not in {
                s.section_id for s in sections
            }:
                structure_ok = False
        signals.append(
            ValidationSignal(
                "structure_valid",
                structure_ok,
                "canonical structure valid" if structure_ok else "canonical structure corrupted",
            )
        )

        any_hard_failed = any(not s.ok for s in signals)
        if any_hard_failed:
            return ParseValidation(
                status="failed",
                signals=signals,
                error_code="PARSE_VALIDATION_FAILED",
                warnings=warnings,
            )

        # --- degraded signals ------------------------------------------------
        degraded_signals = self._degraded_signals(
            parser_result, document, sections, blocks, meaningful_ratio
        )
        signals.extend(degraded_signals)
        for sig in degraded_signals:
            if not sig.ok:
                warnings.append(f"degraded:{sig.name}:{sig.message}")

        if any(not s.ok for s in degraded_signals):
            return ParseValidation(status="degraded", signals=signals, warnings=warnings)
        return ParseValidation(status="passed", signals=signals, warnings=warnings)

    # ------------------------------------------------------------------ helpers

    def _degraded_signals(
        self,
        parser_result: ParserResult,
        document: CanonicalDocument,
        sections: list[CanonicalSection],
        blocks: list[CanonicalBlock],
        meaningful_ratio: float | None,
    ) -> list[ValidationSignal]:
        out: list[ValidationSignal] = []
        config = self._config

        if meaningful_ratio is None:
            out.append(
                ValidationSignal(
                    "meaningful_text_page_ratio",
                    True,
                    "meaningful_text_page_ratio=unknown (no page provenance)",
                )
            )
        else:
            out.append(
                ValidationSignal(
                    "meaningful_text_page_ratio",
                    meaningful_ratio >= config.degraded_if_meaningful_text_page_ratio_below,
                    f"meaningful_text_page_ratio={meaningful_ratio:.3f}",
                )
            )

        # Parser provided no page-level provenance (page<=0 items): readable
        # body with missing provenance is a degraded signal, never an illegal
        # page-number hard failure.
        if blocks and not any(b.pages or b.provenance for b in blocks):
            out.append(
                ValidationSignal(
                    "provenance_missing",
                    False,
                    "parser provided no page/bbox provenance; blocks keep "
                    "text/source_items but carry no pages",
                )
            )

        total_chars = sum(len(b.text) for b in blocks)
        replacement_chars = sum(b.text.count("\ufffd") for b in blocks)
        replacement_ratio = (
            replacement_chars / total_chars if total_chars else 0.0
        )
        out.append(
            ValidationSignal(
                "replacement_char_ratio",
                replacement_ratio <= config.degraded_if_replacement_char_ratio_above,
                f"replacement_char_ratio={replacement_ratio:.4f}",
            )
        )

        duplicate_ratio = self._suspicious_duplicate_ratio(blocks)
        out.append(
            ValidationSignal(
                "suspicious_duplicate_ratio",
                duplicate_ratio <= config.degraded_if_suspicious_duplicate_ratio_above,
                f"suspicious_duplicate_ratio={duplicate_ratio:.3f}",
            )
        )

        if document.page_count > config.degraded_if_zero_headings_min_pages and not sections:
            out.append(
                ValidationSignal(
                    "zero_headings",
                    False,
                    f"multi-page paper ({document.page_count} pages) has no headings",
                )
            )

        empty_pages = self._pages_without_text(blocks, document.page_count)
        if len(empty_pages) >= 2:
            out.append(
                ValidationSignal(
                    "empty_body_pages",
                    False,
                    f"pages with no text: {sorted(empty_pages)}",
                )
            )

        quality = parser_result.parser_quality or {}
        low_quality = False
        for value in quality.values():
            if isinstance(value, str) and value.strip().lower() in ("poor", "low", "failed"):
                low_quality = True
        out.append(
            ValidationSignal("parser_quality", not low_quality, "parser self-reported quality")
        )

        structure_hits = sum(
            len(_STRUCTURE_HINT_RE.findall(b.text)) for b in blocks
        )
        structured_types = {"table", "figure", "caption"}
        if structure_hits and not any(b.block_type in structured_types for b in blocks):
            out.append(
                ValidationSignal(
                    "structure_missing",
                    False,
                    "text references tables/figures but no structured block exists",
                )
            )

        page_sequence = [
            (b.provenance[0].page if b.provenance else b.pages[0] if b.pages else 0)
            for b in blocks
        ]
        jumps = sum(
            1
            for prev, cur in zip(page_sequence, page_sequence[1:])
            if cur < prev
        )
        out.append(
            ValidationSignal(
                "reading_order_jumps",
                jumps <= 1,
                f"reading-order backward page jumps: {jumps}",
            )
        )
        return out

    def _meaningful_text_page_ratio(
        self, blocks: list[CanonicalBlock], page_count: int
    ) -> float | None:
        """Ratio of pages with meaningful text.

        Returns ``None`` (unknown) when a body exists but no block carries
        page-level provenance -- the ratio cannot be computed and must not be
        reported as 0, otherwise an existing body would be misjudged as
        near-empty and every page would look empty.
        """
        if page_count <= 0:
            return 1.0 if blocks else 0.0
        per_page: dict[int, int] = {}
        for block in blocks:
            for prov in block.provenance:
                per_page[prov.page] = per_page.get(prov.page, 0) + len(block.text)
        if blocks and not per_page:
            return None
        meaningful = sum(
            1
            for page in range(1, page_count + 1)
            if per_page.get(page, 0) >= self._config.minimum_meaningful_text_chars
        )
        return meaningful / page_count

    @staticmethod
    def _suspicious_duplicate_ratio(blocks: list[CanonicalBlock]) -> float:
        if not blocks:
            return 0.0
        seen: dict[str, int] = {}
        for block in blocks:
            norm = " ".join(block.text.split())
            if norm:
                seen[norm] = seen.get(norm, 0) + 1
        duplicated_blocks = sum(count for count in seen.values() if count > 1)
        return duplicated_blocks / len(blocks)

    @staticmethod
    def _pages_without_text(blocks: list[CanonicalBlock], page_count: int) -> list[int]:
        per_page: dict[int, int] = {}
        for block in blocks:
            for prov in block.provenance:
                per_page[prov.page] = per_page.get(prov.page, 0) + len(block.text)
        # No provenance data: page emptiness is unknown -- never report every
        # page as empty just because the mapping is missing.
        if blocks and not per_page:
            return []
        return [page for page in range(1, page_count + 1) if per_page.get(page, 0) == 0]


_ALLOWED_SET = frozenset(
    {
        "paragraph",
        "heading",
        "list",
        "table",
        "figure",
        "caption",
        "equation",
        "footnote",
        "reference",
        "other",
    }
)
