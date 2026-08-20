"""Retrieval chunk builder (FR-009).

One shared ``retrieval_chunks.jsonl`` feeds both BM25 and Dense. Chunks never
cross section boundaries, use zero fixed overlap, honor the token bounds
(120/400/650/900), carry ``source_refs`` with exact char ranges into canonical
block text, and split overlong blocks only at the retrieval layer.

Compliance invariants (FR-FIX-001):

- Caption -> Table/Figure/Equation binding uses the Canonical relation
  (parent-side ``relations.caption_block_ids`` preferred, caption-side
  ``relations.parent_block_id`` fallback) and never depends on the caption
  appearing before its parent in reading order.
- Overlarge tables are split into row groups; every group repeats the column
  header and records a parseable ``table_row_start`` / ``table_row_end``
  derived from ``content.cells[].row/row_span`` and ``n_rows``. Without
  reliable cells the fallback text split records ``None`` ranges (never
  fabricated). The Canonical Table block is never modified.
- Every chunk's final ``retrieval_text`` (including ``context_prefix``) stays
  at or below ``hard_max_tokens``. A long ``section_path`` uses a deterministic
  compact ``context_prefix`` (full path preserved in ``section_path``) and the
  body is always budgeted before assembly, with an assembly-time guard as the
  absolute invariant.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from transit_scholar.layer2.config import Layer2Config
from transit_scholar.layer2.schema import (
    CanonicalBlock,
    CanonicalSection,
    RetrievalChunk,
    SourceRef,
)
from transit_scholar.layer2.util import SequentialIds, count_tokens

_SENTENCE_SPLIT = ".!?"


@dataclass
class _Unit:
    blocks: list[CanonicalBlock] = field(default_factory=list)
    body_text: str = ""
    source_refs: list[SourceRef] = field(default_factory=list)
    tokens: int = 0
    block_types: list[str] = field(default_factory=list)
    caption_text: str | None = None
    table_row_start: int | None = None
    table_row_end: int | None = None


class ChunkBuilder:
    """Deterministic retrieval chunk builder over canonical blocks."""

    def __init__(self, config: Layer2Config) -> None:
        self._config = config

    def build(
        self,
        blocks: list[CanonicalBlock],
        sections: list[CanonicalSection],
        *,
        paper_id: str,
        parse_run_id: str,
    ) -> list[RetrievalChunk]:
        chunk_ids = SequentialIds("chunk", 5)
        sections_by_id = {s.section_id: s for s in sections}
        section_paths: dict[str | None, list[str]] = {None: []}
        for sec in sections:
            section_paths[sec.section_id] = _section_path(sec, sections_by_id)

        # Bucket blocks by section preserving reading order.
        buckets: dict[str | None, list[CanonicalBlock]] = {}
        order: list[str | None] = []
        for block in blocks:
            sec_id = block.section_id
            if sec_id not in buckets:
                buckets[sec_id] = []
                order.append(sec_id)
            buckets[sec_id].append(block)

        chunks: list[RetrievalChunk] = []
        for sec_id in order:
            section_path = section_paths.get(sec_id, [])
            units = self._build_units(buckets[sec_id])
            chunks.extend(
                self._chunk_units(
                    units,
                    chunk_ids=chunk_ids,
                    paper_id=paper_id,
                    parse_run_id=parse_run_id,
                    section_id=sec_id,
                    section_path=section_path,
                )
            )
        return chunks

    # ------------------------------------------------------------------ units

    def _build_units(self, blocks: list[CanonicalBlock]) -> list[_Unit]:
        """Group blocks into retrieval units.

        Caption binding is relation-driven: parent-side
        ``relations.caption_block_ids`` is authoritative; when absent the
        caption-side ``relations.parent_block_id`` is used. There is no
        "caption must precede its parent" dependency.
        """
        units: list[_Unit] = []
        captions_by_id = {
            b.block_id: b for b in blocks if b.block_type == "caption"
        }
        children_by_parent: dict[str, list[CanonicalBlock]] = {}
        for block in blocks:
            if block.block_type not in ("table", "figure", "equation"):
                continue
            parent_ids = block.relations.get("caption_block_ids") or []
            children = [
                captions_by_id[caption_id]
                for caption_id in parent_ids
                if caption_id in captions_by_id
            ]
            if not children:
                children = [
                    caption
                    for caption in captions_by_id.values()
                    if caption.relations.get("parent_block_id") == block.block_id
                ]
            children.sort(key=lambda caption: caption.order)
            children_by_parent[block.block_id] = children

        bound_caption_ids: set[str] = set()
        for block in blocks:
            if block.block_type == "caption":
                continue
            if block.block_type in ("table", "figure", "equation"):
                children = children_by_parent.get(block.block_id, [])
                bound_caption_ids.update(c.block_id for c in children)
                units.append(self._structured_unit(block, children))
                continue
            units.append(self._text_unit(block))
        # Captions whose parent is missing or whose relation is dangling become
        # plain text units (deterministic, matching the pre-fix behavior).
        for block in blocks:
            if (
                block.block_type == "caption"
                and block.block_id not in bound_caption_ids
            ):
                units.append(self._text_unit(block))
        return units

    def _text_unit(self, block: CanonicalBlock) -> _Unit:
        text = _block_text(block)
        refs = [SourceRef(block_id=block.block_id, char_start=0, char_end=len(text))]
        return _Unit(
            blocks=[block],
            body_text=text,
            source_refs=refs,
            tokens=count_tokens(text),
            block_types=[block.block_type],
        )

    def _structured_unit(
        self, structured: CanonicalBlock, captions: list[CanonicalBlock]
    ) -> _Unit:
        text = _block_text(structured)
        refs = [
            SourceRef(
                block_id=structured.block_id, char_start=0, char_end=len(text)
            )
        ]
        types = [structured.block_type]
        caption_texts: list[str] = []
        for caption in captions:
            caption_texts.append(caption.text)
            refs.append(
                SourceRef(
                    block_id=caption.block_id,
                    char_start=0,
                    char_end=len(caption.text),
                )
            )
            types.append(caption.block_type)
        body = text
        if caption_texts:
            body = body + "\n\n" + "\n\n".join(caption_texts)
        return _Unit(
            blocks=[structured, *captions],
            body_text=body,
            source_refs=refs,
            tokens=count_tokens(body),
            block_types=types,
            caption_text="\n\n".join(caption_texts) if caption_texts else None,
        )

    # ------------------------------------------------------------------ chunks

    def _chunk_units(
        self,
        units: list[_Unit],
        *,
        chunk_ids: SequentialIds,
        paper_id: str,
        parse_run_id: str,
        section_id: str | None,
        section_path: list[str],
    ) -> list[RetrievalChunk]:
        config = self._config
        prefix_items, prefix_compacted = _effective_prefix(
            section_path, config.hard_max_tokens
        )
        prefix_tokens = count_tokens(" > ".join(prefix_items)) if prefix_items else 0
        body_budget = max(1, config.hard_max_tokens - prefix_tokens)

        chunks: list[RetrievalChunk] = []
        buffer: list[_Unit] = []
        buffer_tokens = 0

        def flush() -> None:
            nonlocal buffer, buffer_tokens
            if buffer:
                chunks.append(
                    self._assemble(
                        buffer,
                        chunk_ids.next_id(),
                        paper_id=paper_id,
                        parse_run_id=parse_run_id,
                        section_id=section_id,
                        section_path=section_path,
                        prefix_items=prefix_items,
                        prefix_compacted=prefix_compacted,
                    )
                )
                buffer = []
                buffer_tokens = 0

        for unit in units:
            if unit.tokens > body_budget:
                flush()
                for fragment in self._split_overlong_unit(unit, body_budget):
                    chunks.append(
                        self._assemble(
                            [fragment],
                            chunk_ids.next_id(),
                            paper_id=paper_id,
                            parse_run_id=parse_run_id,
                            section_id=section_id,
                            section_path=section_path,
                            prefix_items=prefix_items,
                            prefix_compacted=prefix_compacted,
                        )
                    )
                continue
            if buffer and buffer_tokens + unit.tokens > body_budget:
                flush()
            if (
                buffer
                and buffer_tokens + unit.tokens > config.soft_max_tokens
                and buffer_tokens >= config.target_tokens
            ):
                flush()
            buffer.append(unit)
            buffer_tokens += unit.tokens
        flush()
        return chunks

    def _assemble(
        self,
        units: list[_Unit],
        chunk_id: str,
        *,
        paper_id: str,
        parse_run_id: str,
        section_id: str | None,
        section_path: list[str],
        prefix_items: list[str] | None = None,
        prefix_compacted: bool = False,
    ) -> RetrievalChunk:
        pages: list[int] = []
        source_refs: list[SourceRef] = []
        body_parts: list[str] = []
        block_types: list[str] = []
        caption_parts: list[str] = []
        table_rows: list[tuple[int, int]] = []
        for unit in units:
            body_parts.append(unit.body_text)
            source_refs.extend(unit.source_refs)
            block_types.extend(unit.block_types)
            if unit.caption_text:
                caption_parts.append(unit.caption_text)
            if unit.table_row_start is not None and unit.table_row_end is not None:
                table_rows.append((unit.table_row_start, unit.table_row_end))
            for block in unit.blocks:
                pages.extend(block.pages)
        body_text = "\n\n".join(body_parts)
        context_prefix = " > ".join(prefix_items if prefix_items is not None else section_path)
        retrieval_text = (
            context_prefix + "\n\n" + body_text if context_prefix else body_text
        )
        token_count = count_tokens(retrieval_text)

        # Absolute invariant guard (unreachable through the budget-based path):
        # deterministically trim the tail unit(s), then the last unit's body,
        # then the prefix, shrinking source_refs so they stay valid.
        if token_count > self._config.hard_max_tokens:
            body_text, source_refs, context_prefix, token_count, prefix_compacted = (
                _trim_assembled(
                    units,
                    context_prefix,
                    section_path,
                    prefix_compacted,
                    self._config.hard_max_tokens,
                )
            )

        table_row_start = table_rows[0][0] if table_rows else None
        table_row_end = table_rows[-1][1] if table_rows else None
        return RetrievalChunk(
            chunk_id=chunk_id,
            paper_id=paper_id,
            parse_run_id=parse_run_id,
            chunker_version=self._config.chunker_version,
            section_id=section_id,
            section_path=list(section_path),
            pages=sorted(set(pages)),
            source_refs=source_refs,
            body_text=body_text,
            context_prefix=context_prefix,
            retrieval_text=(
                context_prefix + "\n\n" + body_text if context_prefix else body_text
            ),
            token_count=token_count,
            block_types=list(dict.fromkeys(block_types)),
            table_row_start=table_row_start,
            table_row_end=table_row_end,
            caption_text="\n\n".join(dict.fromkeys(caption_parts)) if caption_parts else None,
            context_prefix_compacted=prefix_compacted,
        )

    # ------------------------------------------------------------------ splits

    def _split_overlong_unit(self, unit: _Unit, budget: int) -> list[_Unit]:
        if unit.blocks and unit.blocks[0].block_type == "table":
            return self._split_table_unit(unit, budget)
        fragments: list[_Unit] = []
        for block in unit.blocks:
            text = _block_text(block)
            if count_tokens(text) <= budget:
                fragments.append(self._text_unit(block))
                continue
            for start, end in _split_by_sentence_then_tokens(text, budget):
                fragment_text = text[start:end]
                fragments.append(
                    _Unit(
                        blocks=[block],
                        body_text=fragment_text,
                        source_refs=[
                            SourceRef(
                                block_id=block.block_id,
                                char_start=start,
                                char_end=end,
                            )
                        ],
                        tokens=count_tokens(fragment_text),
                        block_types=[block.block_type],
                    )
                )
        return fragments

    def _split_table_unit(self, unit: _Unit, budget: int) -> list[_Unit]:
        """Split an overlong table into row groups (FR-FIX-001).

        Row-group boundaries and ranges use the canonical ``cells`` rows as
        the source of truth (``n_rows = max(cell.row) + 1``); the markdown
        lines only drive rendering. Every group repeats the column header,
        carries the caption text + ``caption_text`` metadata + caption refs,
        and records a parseable, gap-free ``[table_row_start, table_row_end)``.
        """
        table = unit.blocks[0]
        captions = unit.blocks[1:]
        cells = table.content.get("cells", [])
        markdown_lines = (table.content.get("markdown") or "").split("\n")
        n_rows = _table_n_rows(cells)
        if n_rows is None or len(markdown_lines) < 2:
            return self._split_text_unit(unit, budget)

        header_lines = markdown_lines[:2]
        body_lines = markdown_lines[2:]
        header_tokens = count_tokens("\n".join(header_lines))
        caption_text = "\n\n".join(c.text for c in captions)
        caption_body = ("\n\n" + caption_text) if caption_text else ""
        caption_tokens = count_tokens(caption_body)
        fixed_tokens = header_tokens + caption_tokens

        # Deterministic degradation for an extreme single-row + header + caption
        # case that cannot fit even without data rows: drop caption text from
        # the body (keep ``caption_text`` metadata and refs).
        caption_in_body = fixed_tokens <= budget
        if not caption_in_body:
            fixed_tokens = min(header_tokens, budget)

        groups: list[list[str]] = []
        current: list[str] = []
        current_tokens = fixed_tokens
        for line in body_lines:
            line_tokens = count_tokens(line)
            if current and current_tokens + line_tokens > budget:
                groups.append(current)
                current = [line]
                current_tokens = fixed_tokens + line_tokens
            else:
                current.append(line)
                current_tokens += line_tokens
        if not body_lines or current:
            groups.append(current)

        rows_by_line = [
            min(index + 1, n_rows - 1) for index in range(len(body_lines))
        ]

        fragments: list[_Unit] = []
        table_ref = SourceRef(
            block_id=table.block_id,
            char_start=0,
            char_end=len(table.content.get("markdown") or table.text),
        )
        for index, group in enumerate(groups):
            if not body_lines:
                start_row, end_row = 0, n_rows
            else:
                line_start = sum(len(g) for g in groups[:index])
                line_end = line_start + len(group) - 1
                start_row = 0 if index == 0 else rows_by_line[line_start]
                if index == len(groups) - 1:
                    end_row = n_rows
                else:
                    next_start = rows_by_line[line_end + 1]
                    end_row = min(rows_by_line[line_end] + 1, next_start)
                start_row = min(start_row, n_rows)
                end_row = min(max(end_row, start_row + 1), n_rows)
                if start_row == end_row:
                    end_row = min(start_row + 1, n_rows)

            body = "\n".join([*header_lines, *group])
            refs = [table_ref]
            types = ["table"]
            if caption_text and caption_in_body:
                body = body + caption_body
                refs.extend(
                    SourceRef(
                        block_id=caption.block_id,
                        char_start=0,
                        char_end=len(caption.text),
                    )
                    for caption in captions
                )
                types.append("caption")
            fragments.append(
                _Unit(
                    blocks=[table, *captions] if captions else [table],
                    body_text=body,
                    source_refs=refs,
                    tokens=count_tokens(body),
                    block_types=types,
                    caption_text=caption_text if caption_text else None,
                    table_row_start=start_row,
                    table_row_end=end_row,
                )
            )
        return fragments

    def _split_text_unit(self, unit: _Unit, budget: int) -> list[_Unit]:
        """Fallback for overlong units that are not row-group tables."""
        fragments: list[_Unit] = []
        for block in unit.blocks:
            text = _block_text(block)
            if count_tokens(text) <= budget:
                fragments.append(self._text_unit(block))
                continue
            for start, end in _split_by_sentence_then_tokens(text, budget):
                fragment_text = text[start:end]
                fragments.append(
                    _Unit(
                        blocks=[block],
                        body_text=fragment_text,
                        source_refs=[
                            SourceRef(
                                block_id=block.block_id,
                                char_start=start,
                                char_end=end,
                            )
                        ],
                        tokens=count_tokens(fragment_text),
                        block_types=[block.block_type],
                    )
                )
        return fragments


# ---------------------------------------------------------------------------
# Module-level helpers (deterministic, importable for tests)
# ---------------------------------------------------------------------------


def _block_text(block: CanonicalBlock) -> str:
    if block.block_type == "table":
        return block.content.get("markdown") or block.text
    if block.block_type == "equation":
        return block.content.get("latex") or block.text
    if block.block_type == "figure":
        return ""
    return block.text


def _section_path(sec: CanonicalSection, sections_by_id: dict[str, CanonicalSection]) -> list[str]:
    path: list[str] = []
    current: str | None = sec.section_id
    while current:
        section = sections_by_id.get(current)
        if section is None:
            break
        path.insert(0, section.title)
        current = section.parent_section_id
    return path


def _table_n_rows(cells: list[dict]) -> int | None:
    """Canonical row count: ``max(cell row) + 1`` over ``content.cells``."""
    if not cells:
        return None
    try:
        rows = [int(cell.get("row")) for cell in cells if "row" in cell]
    except (TypeError, ValueError):
        return None
    if not rows:
        return None
    return max(rows) + 1


def _effective_prefix(section_path: list[str], hard_max: int) -> tuple[list[str], bool]:
    """Return ``(prefix_items, compacted)``.

    When the full ``section_path`` already fits the budget the full path is
    used verbatim. Otherwise a deterministic compact prefix keeps the leading
    items that fit (at least the first item); the full path remains available
    to the chunk via ``section_path``.
    """
    if not section_path:
        return [], False
    if count_tokens(" > ".join(section_path)) < hard_max:
        return list(section_path), False
    kept: list[str] = []
    for item in section_path:
        candidate = kept + [item]
        if kept and count_tokens(" > ".join(candidate)) >= hard_max:
            break
        kept.append(item)
    if not kept:
        kept = [section_path[0]]
    return kept, True


def _trim_assembled(
    units: list[_Unit],
    context_prefix: str,
    section_path: list[str],
    prefix_compacted: bool,
    hard_max: int,
) -> tuple[str, list[SourceRef], str, int, bool]:
    """Deterministic assembly-time guard.

    Order of degradation (all recorded through the returned values):
    1. drop trailing units (tail loss, no ref corruption);
    2. trim the last unit's body within its own block texts;
    3. compact the ``context_prefix`` tail path items.
    ``token_count`` is always the recomputed value of the final text.
    """
    prefix_tokens = count_tokens(context_prefix) if context_prefix else 0
    budget = max(1, hard_max - prefix_tokens)

    while len(units) > 1 and count_tokens("\n\n".join(u.body_text for u in units)) > budget:
        units = units[:-1]

    body_text = "\n\n".join(u.body_text for u in units)
    source_refs: list[SourceRef] = []
    for unit in units:
        source_refs.extend(unit.source_refs)
    if count_tokens(body_text) > budget:
        trimmed = _trim_unit_text(units[-1], budget)
        body_text = "\n\n".join(u.body_text for u in units[:-1] + [trimmed])
        source_refs = []
        for unit in units[:-1] + [trimmed]:
            source_refs.extend(unit.source_refs)

    retrieval_text = context_prefix + "\n\n" + body_text if context_prefix else body_text
    if count_tokens(retrieval_text) > hard_max:
        # Trim the context prefix tail (keep at least one item).
        prefix_items = context_prefix.split(" > ") if context_prefix else []
        while len(prefix_items) > 1 and count_tokens(" > ".join(prefix_items)) >= hard_max - count_tokens(body_text):
            prefix_items = prefix_items[:-1]
        context_prefix = " > ".join(prefix_items)
        prefix_compacted = True
        if count_tokens(context_prefix) >= hard_max:
            context_prefix = context_prefix[: max(1, hard_max - count_tokens(body_text))]
            prefix_compacted = True
        retrieval_text = context_prefix + "\n\n" + body_text if context_prefix else body_text

    return body_text, source_refs, context_prefix, count_tokens(retrieval_text), prefix_compacted


def _trim_unit_text(unit: _Unit, budget: int) -> _Unit:
    """Deterministically trim ``unit``'s body to ``budget`` tokens.

    Keeps full earlier blocks and a partial prefix of the last block that still
    fits; refs are rebuilt from the kept text so every char range remains
    valid. Canonical blocks are never modified.
    """
    parts: list[str] = []
    refs: list[SourceRef] = []
    types: list[str] = []
    remaining = budget
    for block in unit.blocks:
        text = _block_text(block)
        if not text:
            continue
        tokens = count_tokens(text)
        if tokens <= remaining:
            parts.append(text)
            refs.append(
                SourceRef(block_id=block.block_id, char_start=0, char_end=len(text))
            )
            types.append(block.block_type)
            remaining -= tokens
            continue
        if remaining <= 0:
            break
        lo, hi = 0, len(text)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if count_tokens(text[:mid]) <= remaining:
                lo = mid
            else:
                hi = mid - 1
        if lo > 0:
            parts.append(text[:lo])
            refs.append(
                SourceRef(block_id=block.block_id, char_start=0, char_end=lo)
            )
            types.append(block.block_type)
        break
    body = "\n\n".join(parts)
    return _Unit(
        blocks=list(unit.blocks),
        body_text=body,
        source_refs=refs,
        tokens=count_tokens(body),
        block_types=types,
        caption_text=unit.caption_text,
        table_row_start=unit.table_row_start,
        table_row_end=unit.table_row_end,
    )


def _split_by_sentence_then_tokens(text: str, max_tokens: int) -> list[tuple[int, int]]:
    """Split ``text`` into ``(start, end)`` ranges each <= ``max_tokens``.

    Prefers sentence boundaries (after ``.`` / ``!`` / ``?``), then falls back
    to a token-proportional character split for fragments that are still too
    long.
    """
    pieces: list[tuple[int, int, str]] = []
    start = 0
    index = 0
    n = len(text)
    while index < n:
        if text[index] in _SENTENCE_SPLIT:
            end = index + 1
            while end < n and text[end] in (" ", "\n"):
                end += 1
            pieces.append((start, end, text[start:end]))
            start = end
            index = end
        else:
            index += 1
    if start < n:
        pieces.append((start, n, text[start:]))
    if not pieces:
        pieces = [(0, n, text)]

    fragments: list[tuple[int, int]] = []
    current_start: int | None = None
    current_end = 0
    current_tokens = 0
    for piece_start, piece_end, piece in pieces:
        piece_tokens = count_tokens(piece)
        if current_start is None:
            current_start = piece_start
            current_end = piece_end
            current_tokens = piece_tokens
        elif current_tokens + piece_tokens > max_tokens:
            fragments.append((current_start, current_end))
            current_start = piece_start
            current_end = piece_end
            current_tokens = piece_tokens
        else:
            current_end = piece_end
            current_tokens += piece_tokens
    if current_start is not None:
        fragments.append((current_start, current_end))

    final: list[tuple[int, int]] = []
    for frag_start, frag_end in fragments:
        if count_tokens(text[frag_start:frag_end]) <= max_tokens or frag_end - frag_start <= 1:
            final.append((frag_start, frag_end))
            continue
        final.extend(
            _split_by_token_proportion(text, frag_start, frag_end, max_tokens)
        )
    return final


def _split_by_token_proportion(
    text: str, start: int, end: int, max_tokens: int
) -> list[tuple[int, int]]:
    segment = text[start:end]
    total_tokens = count_tokens(segment)
    parts = max(1, -(-total_tokens // max_tokens))
    size = max(1, (end - start) // parts)
    ranges: list[tuple[int, int]] = []
    pos = start
    for part in range(parts):
        if part == parts - 1:
            ranges.append((pos, end))
            break
        next_pos = min(end, pos + size)
        ranges.append((pos, next_pos))
        pos = next_pos
    return ranges
