"""Deterministic Canonical Normalizer (FR-005/FR-006).

Transforms normalized ``ParserItem`` records into the Canonical
``Document / Section / Block / Provenance`` representation with stable reading
order, cleaned text, conservative paragraph reconstruction and type-specific
``content``/``relations``. Parser items never equal canonical blocks.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from transit_scholar.layer2.config import Layer2Config
from transit_scholar.layer2.parser.base import ParserItem, ParserResult
from transit_scholar.layer2.schema import (
    CanonicalBlock,
    CanonicalDocument,
    CanonicalProvenance,
    CanonicalSection,
)
from transit_scholar.layer2.util import SequentialIds, now_utc_iso

#: Matches a hyphen soft line-break: word-\nword -> word+word.
_HYPHEN_BREAK_RE = re.compile(r"(\w)-\s+(?=\w)")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
_SPACE_RE = re.compile(r"[ \t\r\n\f\v]+")
_SENTENCE_END = (".", "!", "?", ":", ";")

#: ``new_parse_run_id()`` shape: ``parse_<utc ts>-<hex>``.
_RUN_ID_RE = re.compile(r"parse_(\d{8}T\d{6}Z)-[0-9a-f]{10}")


def _parse_run_created_at(parse_run_id: str) -> str | None:
    """Derive a stable UTC timestamp from a ``parse_<ts>-<hex>`` run id.

    Canonical ``created_at`` must be deterministic for an identical
    parse-run context; when the caller does not supply an explicit run
    creation time, the timestamp encoded in the run id is reused so repeated
    normalizations of the same run stay byte-identical.
    """
    match = _RUN_ID_RE.fullmatch(parse_run_id)
    if match is None:
        return None
    try:
        parsed = datetime.strptime(match.group(1), "%Y%m%dT%H%M%SZ")
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc).isoformat()


def clean_text(raw: str) -> str:
    """Deterministic V1 text cleaning.

    Removes control characters, collapses abnormal whitespace and fixes common
    line-end hyphenation (``reinforce- ment`` -> ``reinforcement``). Meaningful
    punctuation is preserved. Char ranges are recomputed against the cleaned
    output downstream, so provenance mapping stays correct.
    """
    text = _CONTROL_RE.sub(" ", raw)
    text = text.replace("\u00ad", "")
    text = _HYPHEN_BREAK_RE.sub(r"\1", text)
    text = _SPACE_RE.sub(" ", text)
    return text.strip()


def _ends_with_hyphen(text: str) -> bool:
    return text.endswith("-") and len(text) > 1


def _ends_sentence(text: str) -> bool:
    return text.endswith(_SENTENCE_END)


def _clean_item_text(item: ParserItem) -> str:
    """Clean item text, preserving line structure for list items."""
    if item.item_type == "list":
        cleaned_lines = [
            clean_text(line) for line in item.text.split("\n") if clean_text(line)
        ]
        return "\n".join(cleaned_lines)
    return clean_text(item.text)


@dataclass
class NormalizationOutput:
    document: CanonicalDocument
    sections: list[CanonicalSection] = field(default_factory=list)
    blocks: list[CanonicalBlock] = field(default_factory=list)


class Normalizer:
    """Deterministic parser-item -> canonical converter."""

    def __init__(self, config: Layer2Config) -> None:
        self._config = config

    def normalize(
        self,
        parser_result: ParserResult,
        *,
        paper_id: str,
        file_id: str | None,
        source_sha256: str,
        parse_run_id: str,
        page_heights: dict[int, float] | None = None,
        created_at: str | None = None,
    ) -> NormalizationOutput:
        # Reading order is the parser-observed ``order`` (the page field is
        # provenance, and ``page<=0`` means "no page-level source"). Sorting by
        # order first keeps page-0 items in their reading position instead of
        # jumping them ahead of page-true items.
        items = sorted(parser_result.items, key=lambda it: (it.order, it.page))
        for _item in items:
            _item._cleaned = _clean_item_text(_item)

        sections: list[CanonicalSection] = []
        blocks: list[CanonicalBlock] = []
        section_stack: list[tuple[int, str]] = []
        block_ids = SequentialIds("blk", 5)
        section_ids = SequentialIds("sec", 3)
        current_section_id: str | None = None

        def _open_section(item: ParserItem, heading_block: CanonicalBlock) -> str:
            level = max(1, item.level)
            while section_stack and section_stack[-1][0] >= level:
                section_stack.pop()
            parent_id = section_stack[-1][1] if section_stack else None
            sec_id = section_ids.next_id()
            section_stack.append((level, sec_id))
            sections.append(
                CanonicalSection(
                    section_id=sec_id,
                    paper_id=paper_id,
                    title=heading_block.text,
                    level=level,
                    parent_section_id=parent_id,
                    order=len(sections) + 1,
                    heading_block_id=heading_block.block_id,
                )
            )
            return sec_id

        # --- group items into conservative merge units ----------------------
        groups: list[list[ParserItem]] = []
        current: list[ParserItem] = []
        for index, item in enumerate(items):
            if item.item_type == "heading":
                if current:
                    groups.append(current)
                    current = []
                groups.append([item])
                continue
            if (
                current
                and current[-1].item_type == "paragraph"
                and item.item_type == "paragraph"
                and self.should_merge(
                    current[-1], item, page_heights=page_heights
                )
            ):
                current.append(item)
                continue
            if current:
                groups.append(current)
            current = [item]
        if current:
            groups.append(current)

        # --- build canonical blocks -----------------------------------------
        for group in groups:
            first = group[0]
            if first.item_type == "heading":
                block = self._make_base_block(
                    first, block_ids, paper_id, None
                )
                current_section_id = _open_section(first, block)
                block.section_id = current_section_id
                blocks.append(block)
                continue

            block = self._make_base_block(first, block_ids, paper_id, current_section_id)
            for item in group[1:]:
                item_text = getattr(item, "_cleaned", clean_text(item.text))
                seg_start = len(block.text)
                join = "" if _ends_with_hyphen(block.text) else " "
                block.text = block.text + join + item_text
                if item.page > 0:
                    block.provenance.append(
                        CanonicalProvenance(
                            page=item.page,
                            bbox=item.bbox,
                            source_item_id=item.source_item_id,
                            char_start=seg_start,
                            char_end=len(block.text),
                        )
                    )
                block.source_items.append(item.source_item_id or item.item_id)
            block.pages = sorted({p.page for p in block.provenance})
            blocks.append(block)

        self._link_captions(blocks)

        for order, block in enumerate(blocks, start=1):
            block.order = order

        section_count = len(sections)
        block_count = len(blocks)
        document = CanonicalDocument(
            paper_id=paper_id,
            file_id=file_id,
            source_sha256=source_sha256,
            parse_run_id=parse_run_id,
            parser_name=parser_result.info.name if parser_result.info else "unknown",
            parser_version=(
                parser_result.info.version if parser_result.info else "unknown"
            ),
            parser_config_hash=(
                parser_result.info.config_hash if parser_result.info else ""
            ),
            canonical_schema_version=self._config.canonical_schema_version,
            normalizer_version=self._config.normalizer_version,
            page_count=parser_result.page_count
            if parser_result.page_count
            else (max((p.page for b in blocks for p in b.provenance), default=0)),
            language="en",
            section_count=section_count,
            block_count=block_count,
            parse_status="passed",
            created_at=created_at or _parse_run_created_at(parse_run_id) or now_utc_iso(),
        )
        return NormalizationOutput(document=document, sections=sections, blocks=blocks)

    # ------------------------------------------------------------------ helpers

    def _make_base_block(
        self,
        item: ParserItem,
        block_ids: SequentialIds,
        paper_id: str,
        section_id: str | None,
    ) -> CanonicalBlock:
        text = getattr(item, "_cleaned", clean_text(item.text))
        # ``item.page <= 0`` means the parser provided no page-level source:
        # the block keeps its text and source_items but no provenance entry
        # (``pages=[]`` / ``provenance=[]``), instead of fabricating page 0.
        block = CanonicalBlock(
            block_id=block_ids.next_id(),
            paper_id=paper_id,
            block_type=item.item_type,
            section_id=section_id,
            order=0,
            text=text,
            pages=[item.page] if item.page > 0 else [],
            provenance=(
                [
                    CanonicalProvenance(
                        page=item.page,
                        bbox=item.bbox,
                        source_item_id=item.source_item_id,
                        char_start=0,
                        char_end=len(text),
                    )
                ]
                if item.page > 0
                else []
            ),
            source_items=[item.source_item_id or item.item_id],
        )
        self._apply_type_content(block, item)
        return block

    def _apply_type_content(self, block: CanonicalBlock, item: ParserItem) -> None:
        content = dict(item.content)
        if block.block_type == "table":
            block.content = {
                "label": content.get("label", ""),
                "n_rows": int(content.get("n_rows", 0)),
                "n_cols": int(content.get("n_cols", 0)),
                "cells": content.get("cells", []),
                "markdown": content.get("markdown", block.text),
            }
            if content.get("markdown"):
                block.text = str(content["markdown"])
            if block.provenance:
                block.provenance[0].char_end = len(block.text)
            return
        if block.block_type == "equation":
            block.content = {
                "latex": content.get("latex", block.text),
                "label": content.get("label", ""),
                "raw_text": content.get("raw_text", block.text),
            }
            block.text = block.content["latex"]
            if block.provenance:
                block.provenance[0].char_end = len(block.text)
            return
        if block.block_type == "figure":
            block.content = {
                "label": content.get("label", ""),
                "asset_path": content.get("asset_path", ""),
            }
            for private_key in ("_image_bytes", "_image_ext"):
                if private_key in content:
                    block.content[private_key] = content[private_key]
            block.text = ""
            if block.provenance:
                block.provenance[0].char_end = 0
            return
        block.content = {}

    def _link_captions(self, blocks: list[CanonicalBlock]) -> None:
        """Bidirectional caption<->parent relation (FR-006).

        A caption whose immediately preceding block in reading order is a
        table / figure / equation becomes its caption block.
        """
        last_structured: CanonicalBlock | None = None
        for block in blocks:
            if block.block_type == "caption":
                if last_structured is not None:
                    parent_id = last_structured.block_id
                    block.relations["parent_block_id"] = parent_id
                    caption_ids = list(
                        last_structured.relations.get("caption_block_ids", [])
                    )
                    if block.block_id not in caption_ids:
                        caption_ids.append(block.block_id)
                    last_structured.relations["caption_block_ids"] = caption_ids
                continue
            if block.block_type in ("table", "figure", "equation"):
                last_structured = block
            else:
                last_structured = None

    # ------------------------------------------------------------------ merging

    def should_merge(
        self,
        item_a: ParserItem,
        item_b: ParserItem,
        page_heights: dict[int, float] | None = None,
    ) -> bool:
        """Conservative paragraph reconstruction decision (FR-005 §7).

        Merges only when every continuation condition holds; any ambiguous
        boundary stays split ("keep two smaller paragraphs").
        """
        if item_a.item_type != "paragraph" or item_b.item_type != "paragraph":
            return False
        text_a = getattr(item_a, "_cleaned", _clean_item_text(item_a))
        text_b = getattr(item_b, "_cleaned", _clean_item_text(item_b))
        if not self._font_compatible(item_a, item_b):
            return False
        if not self._geometry_compatible(item_a, item_b, page_heights):
            return False
        cross_page = item_b.page > item_a.page
        if not self._text_signals_allow(text_a, item_a, item_b, cross_page):
            return False
        return True

    @staticmethod
    def _font_compatible(a: ParserItem, b: ParserItem) -> bool:
        if not a.font_size or not b.font_size:
            return True
        larger = max(a.font_size, b.font_size)
        return abs(a.font_size - b.font_size) <= max(0.8, 0.15 * larger)

    @staticmethod
    def _geometry_compatible(
        a: ParserItem, b: ParserItem, page_heights: dict[int, float] | None
    ) -> bool:
        if not a.bbox or not b.bbox:
            return True
        x0_a, y0_a, _x1_a, y1_a = a.bbox
        x0_b, y0_b, _x1_b, _y1_b = b.bbox
        if abs(x0_a - x0_b) > 20.0:
            return False
        if b.page == a.page:
            gap = y0_b - y1_a
            size = a.font_size or 11.0
            if gap > max(40.0, 3.5 * size):
                return False
            return True
        if page_heights:
            height_a = page_heights.get(a.page)
            height_b = page_heights.get(b.page)
            if height_a and y1_a < height_a * 0.5:
                return False
            if height_b and y0_b > height_b * 0.5:
                return False
        return True

    @staticmethod
    def _text_signals_allow(
        text_a: str, a: ParserItem, b: ParserItem, cross_page: bool
    ) -> bool:
        a_end = text_a.rstrip()
        if _ends_with_hyphen(a_end):
            return True
        if _ends_sentence(a_end):
            # Conservative default: a sentence-ending punctuation marks a
            # paragraph boundary even when the next item starts at the same
            # margin (indent is only one possible boundary signal).
            return False
        # Mid-sentence continuation is a strong signal on the same page or
        # across a page break (geometry checks still apply).
        return True
