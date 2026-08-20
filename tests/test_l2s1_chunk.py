"""Layer2 Step1 chunk tests (AC-L2S1-CHUNK-01..05, task T-01..T-03)."""

from __future__ import annotations

import json

from transit_scholar.layer2.schema import RetrievalChunk, SourceRef
from transit_scholar.layer2.util import count_tokens
from tests.l2s1_fixtures import (
    big_table_items,
    canonical_fixture_items,
    deep_section_items,
    long_caption_items,
    read_artifacts,
    run_parse,
    table_caption_items,
)

CHUNK_FIELDS = {
    "chunk_id",
    "paper_id",
    "parse_run_id",
    "chunker_version",
    "section_id",
    "section_path",
    "pages",
    "source_refs",
    "body_text",
    "context_prefix",
    "retrieval_text",
    "token_count",
    "block_types",
    "table_row_start",
    "table_row_end",
    "caption_text",
    "context_prefix_compacted",
}


def _chunks(artifacts) -> list[dict]:
    return [
        json.loads(line) for line in artifacts["chunks"].splitlines() if line.strip()
    ]


def _blocks_by_id(artifacts):
    return {b.block_id: b for b in artifacts["blocks"]}


def test_chunk_schema_and_source_refs_resolve(
    project_tmp_path, monkeypatch, l2_config
):
    """AC-L2S1-CHUNK-01: chunk schema keys present; every source_refs char range
    slices the referenced canonical block's text."""
    _, _, _, result = run_parse(
        project_tmp_path, canonical_fixture_items(), monkeypatch=monkeypatch, page_count=1
    )
    artifacts = read_artifacts(l2_config, result.paper_id, result.parse_run_id)
    blocks = _blocks_by_id(artifacts)

    chunks = _chunks(artifacts)
    assert chunks
    for chunk in chunks:
        assert set(chunk.keys()) == CHUNK_FIELDS
        for ref in chunk["source_refs"]:
            block = blocks[ref["block_id"]]
            assert 0 <= ref["char_start"] <= ref["char_end"] <= len(block.text)
            if block.text:
                assert ref["char_start"] < ref["char_end"]
                assert block.text[ref["char_start"]:ref["char_end"]] != ""


def test_chunk_shared_source_and_token_bounds(
    project_tmp_path, monkeypatch, l2_config
):
    """AC-L2S1-CHUNK-02: one chunk source feeds BM25 and Dense; token bounds
    honor min/target/soft/hard; nothing exceeds hard_max without a split."""
    paragraphs = []
    for i in range(14):
        words = " ".join(f"token{i}_{j}" for j in range(45))
        paragraphs.append(
            {
                "item_id": f"p{i}",
                "item_type": "paragraph",
                "text": f"{words}.",
                "order": i,
                "page": 1,
                "bbox": [70.0, 100.0, 530.0, 120.0],
            }
        )
    items = [
        {
            "item_id": "h",
            "item_type": "heading",
            "text": "Big Section",
            "order": 0,
            "page": 1,
            "level": 1,
            "bbox": [70.0, 60.0, 530.0, 80.0],
        },
        *paragraphs,
    ]
    from transit_scholar.layer2.parser.fake import make_item

    parser_items = [make_item(**item) for item in items]
    _, _, _, result = run_parse(
        project_tmp_path, parser_items, monkeypatch=monkeypatch, page_count=1
    )
    artifacts = read_artifacts(l2_config, result.paper_id, result.parse_run_id)
    chunks = _chunks(artifacts)

    assert len(chunks) >= 2
    config = l2_config
    for chunk in chunks:
        assert chunk["token_count"] <= config.hard_max_tokens
    assert any(chunk["token_count"] >= config.target_tokens for chunk in chunks)
    # chunks below min_tokens are allowed only when the section cannot fill:
    # they must be the last chunk of their section.
    section_groups: dict[str, list] = {}
    for chunk in chunks:
        section_groups.setdefault(chunk["section_id"], []).append(chunk)
    for section_chunks in section_groups.values():
        for index, chunk in enumerate(section_chunks):
            if chunk["token_count"] < config.min_tokens:
                assert index == len(section_chunks) - 1
    # shared chunk source for both retrievers: a single retrieval_chunks.jsonl
    assert (artifacts["run_dir"] / "retrieval_chunks.jsonl").is_file()


def test_chunk_never_crosses_section(project_tmp_path, monkeypatch, l2_config):
    """AC-L2S1-CHUNK-03: no chunk crosses a section boundary; section_path is
    used as context_prefix."""
    from transit_scholar.layer2.parser.fake import make_item

    items = [
        make_item(item_id="h1", item_type="heading", text="Section One", order=0, page=1, level=1, bbox=[70, 60, 530, 80]),
        make_item(item_id="p1", item_type="paragraph", text="Short text for section one.", order=1, page=1, bbox=[70, 100, 530, 120]),
        make_item(item_id="h2", item_type="heading", text="Section Two", order=2, page=1, level=1, bbox=[70, 140, 530, 160]),
        make_item(item_id="p2", item_type="paragraph", text="Short text for section two.", order=3, page=1, bbox=[70, 180, 530, 200]),
    ]
    _, _, _, result = run_parse(project_tmp_path, items, monkeypatch=monkeypatch, page_count=1)
    artifacts = read_artifacts(l2_config, result.paper_id, result.parse_run_id)
    chunks = _chunks(artifacts)

    section_ids = {c["section_id"] for c in chunks}
    assert len(section_ids) == 2
    for chunk in chunks:
        section_path = chunk["section_path"]
        assert section_path
        assert chunk["context_prefix"] == " > ".join(section_path)
    for chunk in chunks:
        if chunk["section_id"]:
            chunk_sections = {b.section_id for b in artifacts["blocks"]}
            assert chunk["section_id"] in chunk_sections


def test_chunk_overlong_block_split_at_retrieval_layer(
    project_tmp_path, monkeypatch, l2_config
):
    """AC-L2S1-CHUNK-04: an overlong canonical block is split only at the
    retrieval layer; canonical blocks.jsonl is unchanged; fragments express
    exact non-overlapping ordered char ranges."""
    from transit_scholar.layer2.parser.fake import make_item

    words = " ".join(f"word{i}" for i in range(1300)) + "."
    items = [
        make_item(
            item_id="p1", item_type="paragraph", text=words,
            order=0, page=1, bbox=[70.0, 100.0, 530.0, 400.0],
        )
    ]
    _, _, _, result = run_parse(project_tmp_path, items, monkeypatch=monkeypatch, page_count=1)
    artifacts = read_artifacts(l2_config, result.paper_id, result.parse_run_id)
    block = artifacts["blocks"][0]
    canonical_before = artifacts["blocks"]

    chunks = _chunks(artifacts)
    fragments = [
        c for c in chunks
        if any(ref["block_id"] == block.block_id for ref in c["source_refs"])
    ]
    assert len(fragments) >= 2

    ranges = []
    for chunk in fragments:
        for ref in chunk["source_refs"]:
            if ref["block_id"] == block.block_id:
                ranges.append((ref["char_start"], ref["char_end"]))
                assert chunk["token_count"] <= l2_config.hard_max_tokens
    ranges.sort()
    # non-overlapping and ordered
    for (s1, e1), (s2, e2) in zip(ranges, ranges[1:]):
        assert e1 <= s2
    assert ranges[0][0] == 0
    assert ranges[-1][1] == len(block.text)

    # canonical unchanged (block text intact, single block)
    assert artifacts["blocks"] == canonical_before
    assert len([b for b in artifacts["blocks"]]) == 1


def test_chunk_table_caption_and_figure_caption_evidence(
    project_tmp_path, monkeypatch, l2_config
):
    """AC-L2S1-CHUNK-05: a small table + its caption become one chunk with both
    bodies; figure captions appear in chunk text with their evidence chain."""
    _, _, _, result = run_parse(
        project_tmp_path, table_caption_items(), monkeypatch=monkeypatch, page_count=3
    )
    artifacts = read_artifacts(l2_config, result.paper_id, result.parse_run_id)
    chunks = _chunks(artifacts)
    blocks = _blocks_by_id(artifacts)
    table = [b for b in artifacts["blocks"] if b.block_type == "table"][0]
    figure = [b for b in artifacts["blocks"] if b.block_type == "figure"][0]
    captions = [b for b in artifacts["blocks"] if b.block_type == "caption"]

    table_chunks = [
        c for c in chunks
        if any(ref["block_id"] == table.block_id for ref in c["source_refs"])
    ]
    assert table_chunks, "no chunk references the table block"
    table_chunk = table_chunks[0]
    assert table_chunk["token_count"] <= l2_config.hard_max_tokens
    # table markdown + caption text both present
    assert "| Method | Mean Wait |" in table_chunk["body_text"]
    assert any(cap.text in table_chunk["body_text"] for cap in captions)

    figure_chunks = [
        c for c in chunks
        if any(ref["block_id"] == figure.block_id for ref in c["source_refs"])
    ]
    assert figure_chunks, "no chunk references the figure block"
    figure_chunk = figure_chunks[0]
    assert "Figure 1. Average waiting time" in figure_chunk["body_text"]
    # evidence chain intact: source_refs resolve to real blocks
    for ref in figure_chunk["source_refs"]:
        assert ref["block_id"] in blocks


# ---------------------------------------------------------------------------
# task-2026-08-13-001 T-01 / T-02 / T-03 (FR-FIX-001)
# ---------------------------------------------------------------------------


def test_t01_overlarge_table_row_groups_with_ranges(
    project_tmp_path, monkeypatch, l2_config
):
    """T-01 (AC-FIX-001): a >900-token table is split into >= 2 row-group
    chunks, each repeating the column header and carrying parseable
    table_row_start/end; the ranges stitch to a gap-free [0, n_rows); the
    Canonical Table block bytes are untouched."""
    from transit_scholar.layer2.parser.fake import make_item as _make_item

    items = big_table_items(data_rows=25, columns=3, tokens_per_cell=14)
    table_item = items[0]
    markdown = table_item.content["markdown"]
    cells = table_item.content["cells"]
    n_rows = max(int(cell["row"]) for cell in cells) + 1
    assert len(cells) >= 20
    assert count_tokens(markdown) > 900

    _, _, _, result = run_parse(
        project_tmp_path, items, monkeypatch=monkeypatch, page_count=2
    )
    artifacts = read_artifacts(l2_config, result.paper_id, result.parse_run_id)
    blocks = artifacts["blocks"]
    table_block = [b for b in blocks if b.block_type == "table"][0]
    canonical_before = table_block.to_dict()
    chunks = _chunks(artifacts)
    table_chunks = [
        c for c in chunks
        if any(ref["block_id"] == table_block.block_id for ref in c["source_refs"])
    ]
    assert len(table_chunks) >= 2

    header_line = f"| Col 0 | Col 1 | Col 2 |"
    for chunk in table_chunks:
        assert chunk["body_text"].startswith(header_line)
        start, end = chunk["table_row_start"], chunk["table_row_end"]
        assert isinstance(start, int) and isinstance(end, int)
        assert 0 <= start < end <= n_rows

    ranges = sorted(
        (c["table_row_start"], c["table_row_end"]) for c in table_chunks
    )
    assert ranges[0][0] == 0
    assert ranges[-1][1] == n_rows
    for (s1, e1), (s2, e2) in zip(ranges, ranges[1:]):
        assert e1 == s2
    assert ranges[0][0] == 0 and ranges[-1][1] == n_rows
    # coverage is exactly [0, n_rows)
    covered = [False] * n_rows
    for start, end in ranges:
        for row in range(start, end):
            covered[row] = True
    assert all(covered)

    # canonical table block unchanged
    table_after = [b for b in artifacts["blocks"] if b.block_type == "table"][0]
    assert table_after.to_dict() == canonical_before
    assert table_after.content["markdown"] == markdown
    assert table_after.content["cells"] == cells


def test_t01_table_without_reliable_cells_has_null_ranges(
    project_tmp_path, monkeypatch, l2_config
):
    """T-01 fallback: a table whose markdown has no canonical cells is split by
    text with table_row_start/end = None (never fabricated)."""
    words = " ".join(f"cell{i}" for i in range(950))
    markdown = f"| H |\n| --- |\n| {words} |"
    items = [
        {
            "item_id": "tbl_no_cells", "item_type": "table", "text": markdown,
            "order": 0, "page": 1, "bbox": [70.0, 60.0, 530.0, 120.0],
            "content": {"label": "Table NC", "n_rows": 2, "n_cols": 1,
                        "cells": [], "markdown": markdown},
        }
    ]
    from transit_scholar.layer2.parser.fake import make_item

    _, _, _, result = run_parse(
        project_tmp_path, [make_item(**item) for item in items],
        monkeypatch=monkeypatch, page_count=1,
    )
    artifacts = read_artifacts(l2_config, result.paper_id, result.parse_run_id)
    table_block = [b for b in artifacts["blocks"] if b.block_type == "table"][0]
    chunks = [
        c for c in _chunks(artifacts)
        if any(ref["block_id"] == table_block.block_id for ref in c["source_refs"])
    ]
    assert len(chunks) >= 2
    for chunk in chunks:
        assert chunk["table_row_start"] is None
        assert chunk["table_row_end"] is None
        assert chunk["token_count"] <= l2_config.hard_max_tokens


def test_t02_caption_binds_via_relation_after_parent(
    project_tmp_path, monkeypatch, l2_config
):
    """T-02 (AC-FIX-002): captions that appear AFTER their parent still bind
    through canonical relations into the same retrieval unit; every row-group
    chunk of an over-large table carries the caption text (metadata) and the
    caption's source ref."""
    items = big_table_items(data_rows=25, columns=2, tokens_per_cell=10)
    assert items[0].item_type == "table"
    assert items[1].item_type == "caption"  # caption follows parent
    _, _, _, result = run_parse(
        project_tmp_path, items, monkeypatch=monkeypatch, page_count=2
    )
    artifacts = read_artifacts(l2_config, result.paper_id, result.parse_run_id)
    table_block = [b for b in artifacts["blocks"] if b.block_type == "table"][0]
    caption_blocks = [b for b in artifacts["blocks"] if b.block_type == "caption"]
    assert caption_blocks, "fixture caption must become a canonical block"
    assert caption_blocks[0].relations.get("parent_block_id") == table_block.block_id
    assert table_block.relations.get("caption_block_ids") == [
        caption_blocks[0].block_id
    ]

    chunks = _chunks(artifacts)
    table_chunks = [
        c for c in chunks
        if any(ref["block_id"] == table_block.block_id for ref in c["source_refs"])
    ]
    assert len(table_chunks) >= 2
    for chunk in table_chunks:
        assert chunk["caption_text"] and "Table Big" in chunk["caption_text"]
        caption_refs = [
            ref for ref in chunk["source_refs"]
            if ref["block_id"] == caption_blocks[0].block_id
        ]
        assert caption_refs, "every row-group chunk must reference the caption"
        assert caption_refs[0]["char_start"] < caption_refs[0]["char_end"]

    # caption text also stably present in the retrieval text of every chunk
    for chunk in table_chunks:
        assert "Table Big" in chunk["body_text"]


def test_t02_small_table_figure_caption_same_chunk(
    project_tmp_path, monkeypatch, l2_config
):
    """T-02 (AC-FIX-002.3): capacity-permitting, a small table/figure shares a
    chunk with its caption; the chunk's source_refs contain both."""
    _, _, _, result = run_parse(
        project_tmp_path, table_caption_items(), monkeypatch=monkeypatch, page_count=3
    )
    artifacts = read_artifacts(l2_config, result.paper_id, result.parse_run_id)
    blocks = {b.block_id: b for b in artifacts["blocks"]}
    table = [b for b in artifacts["blocks"] if b.block_type == "table"][0]
    figure = [b for b in artifacts["blocks"] if b.block_type == "figure"][0]
    chunks = _chunks(artifacts)

    for structured in (table, figure):
        captions = [
            blocks[caption_id]
            for caption_id in (structured.relations.get("caption_block_ids") or [])
            if caption_id in blocks
        ]
        assert captions, "relation must exist for the structured block"
        chunk = next(
            c for c in chunks
            if any(ref["block_id"] == structured.block_id for ref in c["source_refs"])
        )
        ref_ids = {ref["block_id"] for ref in chunk["source_refs"]}
        assert structured.block_id in ref_ids
        assert all(caption.block_id in ref_ids for caption in captions)
        assert chunk["caption_text"] and any(
            caption.text in chunk["caption_text"] for caption in captions
        )


def test_t03_every_chunk_under_hard_max_tokens(
    project_tmp_path, monkeypatch, l2_config
):
    """T-03 (AC-FIX-003): every produced chunk satisfies
    count_tokens(retrieval_text) <= 900 and JSON token_count equals the
    recomputed value -- across overlong paragraphs, over-large tables, deep
    long section paths and long captions + equations."""
    from transit_scholar.layer2.parser.fake import make_item as _make_item

    overlong_words = " ".join(f"w{i}" for i in range(1200)) + "."
    fixtures = {
        "big_table": big_table_items(data_rows=25, columns=3, tokens_per_cell=14),
        "deep_section": deep_section_items(long_title_tokens=320),
        "long_caption": long_caption_items(),
        "overlong_paragraph": [
            _make_item(
                item_id="p_ol", item_type="paragraph", text=overlong_words,
                order=0, page=1, bbox=[70.0, 100.0, 530.0, 400.0],
            )
        ],
    }
    for name, items in fixtures.items():
        _, _, _, result = run_parse(
            project_tmp_path, items, monkeypatch=monkeypatch, page_count=2
        )
        artifacts = read_artifacts(l2_config, result.paper_id, result.parse_run_id)
        chunks = _chunks(artifacts)
        assert chunks, f"fixture {name} produced no chunks"
        for chunk in chunks:
            recomputed = count_tokens(chunk["retrieval_text"])
            assert chunk["token_count"] == recomputed, (
                f"{name}: token_count {chunk['token_count']} != recomputed {recomputed}"
            )
            assert recomputed <= l2_config.hard_max_tokens, (
                f"{name}: chunk {chunk['chunk_id']} has {recomputed} tokens"
            )


def test_t03_deep_section_compact_prefix_preserves_full_path(
    project_tmp_path, monkeypatch, l2_config
):
    """T-03 (P revision 5): when the full section_path cannot fit the budget the
    chunk uses a deterministic compact context_prefix, marks
    context_prefix_compacted and still preserves the complete section_path."""
    _, _, _, result = run_parse(
        project_tmp_path,
        deep_section_items(long_title_tokens=320),
        monkeypatch=monkeypatch,
        page_count=1,
    )
    artifacts = read_artifacts(l2_config, result.paper_id, result.parse_run_id)
    chunks = _chunks(artifacts)
    compacted = [c for c in chunks if c["context_prefix_compacted"]]
    assert compacted, "a long section_path must produce compacted prefixes"
    for chunk in compacted:
        assert len(chunk["section_path"]) == 3  # full path preserved
        assert count_tokens(" > ".join(chunk["section_path"])) > 900
        assert chunk["context_prefix"] != " > ".join(chunk["section_path"])
        assert count_tokens(chunk["retrieval_text"]) <= l2_config.hard_max_tokens
        # compact prefix is a deterministic leading subsequence
        retained = chunk["context_prefix"].split(" > ")
        assert retained == chunk["section_path"][: len(retained)]
    # non-compacted sections keep the exact joined path as context_prefix
    for chunk in chunks:
        if not chunk["context_prefix_compacted"]:
            assert chunk["context_prefix"] == " > ".join(chunk["section_path"])
