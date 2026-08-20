"""Clean Markdown view + ``markdown_map.jsonl`` (FR-008).

``paper.md`` contains only the rendered document -- no block ids, no bbox, no
page markers, no parse-run ids. ``markdown_map.jsonl`` maps markdown line
ranges back to canonical ``block_id``s with gapless, non-overlapping coverage
so a grep hit can be traced: line -> map -> block -> provenance -> PDF page.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from transit_scholar.layer2.config import Layer2Config
from transit_scholar.layer2.schema import (
    CanonicalBlock,
    CanonicalSection,
    MarkdownMapEntry,
)

FIGURE_ASSET_PREFIX = "assets/figures/"


@dataclass
class MarkdownOutput:
    text: str
    lines: list[str]
    entries: list[MarkdownMapEntry]


class MarkdownRenderer:
    """Render canonical blocks to clean markdown with a sidecar line map."""

    def __init__(self, config: Layer2Config) -> None:
        self._config = config

    def render(
        self,
        blocks: list[CanonicalBlock],
        sections: list[CanonicalSection],
        parse_run_id: str,
    ) -> MarkdownOutput:
        sections_by_id = {s.section_id: s for s in sections}
        lines: list[str] = []
        entries: list[MarkdownMapEntry] = []

        for block in blocks:
            block_lines = self._render_block(block, sections_by_id)
            if not block_lines:
                continue
            start = len(lines) + 1
            lines.extend(block_lines)
            lines.append("")  # blank separator attributed to this block
            end = len(lines)
            entries.append(
                MarkdownMapEntry(
                    md_line_start=start,
                    md_line_end=end,
                    block_ids=[block.block_id],
                    parse_run_id=parse_run_id,
                    renderer_version=self._config.renderer_version,
                )
            )

        text = "\n".join(lines)
        return MarkdownOutput(text=text, lines=list(lines), entries=entries)

    # ------------------------------------------------------------------ helpers

    def _render_block(
        self, block: CanonicalBlock, sections_by_id: dict[str, CanonicalSection]
    ) -> list[str]:
        block_type = block.block_type
        if block_type == "heading":
            section = sections_by_id.get(block.section_id or "")
            level = section.level if section else 1
            level = max(1, min(level, 6))
            return [f"{'#' * level} {block.text}"]
        if block_type == "paragraph":
            return _split_lines(block.text)
        if block_type in ("footnote", "reference", "other"):
            return _split_lines(block.text)
        if block_type == "list":
            return ["- " + line for line in _split_lines(block.text)]
        if block_type == "equation":
            latex = block.content.get("latex") or block.text
            label = block.content.get("label") or ""
            rendered = ["$$", latex, "$$"]
            if label:
                rendered.append(label)
            return rendered
        if block_type == "table":
            return _split_lines(block.content.get("markdown") or block.text)
        if block_type == "figure":
            label = block.content.get("label", "")
            asset_path = block.content.get("asset_path", "")
            if not asset_path and label:
                asset_path = f"{FIGURE_ASSET_PREFIX}{_slugify(label)}"
            if not label and not asset_path:
                return []
            return [f"![{label}]({asset_path})"]
        if block_type == "caption":
            return _split_lines(block.text)
        return _split_lines(block.text)


def _split_lines(text: str) -> list[str]:
    return [line.rstrip() for line in text.split("\n")]


def _slugify(label: str) -> str:
    keep = "".join(ch if ch.isalnum() else "_" for ch in label.lower())
    return (keep.strip("_") or "fig") + ".png"
