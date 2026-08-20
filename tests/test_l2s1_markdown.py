"""Layer2 Step1 markdown tests (AC-L2S1-MARKDOWN-01..05)."""

from __future__ import annotations

import json
import re

from transit_scholar.layer2.markdown import MarkdownRenderer
from transit_scholar.layer2.parser.fake import FakeParserAdapter, make_item
from tests.l2s1_fixtures import (
    canonical_fixture_items,
    equation_items,
    read_artifacts,
    run_parse,
    table_caption_items,
)

MACHINE_METADATA_RE = re.compile(r"(blk_\d{3,}|bbox|parse_run_id|md_line_start|page \d+ marker|assets/figures/fig_\d+\.png)")


def test_markdown_clean_no_machine_metadata(project_tmp_path, monkeypatch, l2_config):
    """AC-L2S1-MARKDOWN-01: paper.md contains no block_id/bbox/page markers;
    headings render at the section level."""
    _, _, _, result = run_parse(
        project_tmp_path, canonical_fixture_items(), monkeypatch=monkeypatch, page_count=1
    )
    artifacts = read_artifacts(l2_config, result.paper_id, result.parse_run_id)
    md = artifacts["markdown"]
    assert "blk_" not in md
    assert "bbox" not in md
    assert "parse_run_id" not in md
    assert MACHINE_METADATA_RE.search(md) is None
    # headings at correct levels
    assert "# Introduction" in md
    assert "# Method" in md
    assert "## " not in md.split("# Method")[0]


def test_markdown_basic_renderings(project_tmp_path, monkeypatch, l2_config):
    """AC-L2S1-MARKDOWN-02: paragraph, list, equation, table, figure, caption
    each have a basic rendering."""
    cells = [
        {"row": 0, "col": 0, "row_span": 1, "col_span": 1, "text": "Method", "is_header": True},
        {"row": 0, "col": 1, "row_span": 1, "col_span": 1, "text": "Wait", "is_header": True},
        {"row": 1, "col": 0, "row_span": 1, "col_span": 1, "text": "DRL", "is_header": False},
        {"row": 1, "col": 1, "row_span": 1, "col_span": 1, "text": "5.1", "is_header": False},
    ]
    table_markdown = "| Method | Wait |\n| --- | --- |\n| DRL | 5.1 |"
    items = [
        make_item(item_id="p", item_type="paragraph", text="A plain paragraph.", order=0, page=1, bbox=[70, 100, 530, 120]),
        make_item(item_id="l", item_type="list", text="first item\nsecond item", order=1, page=1, bbox=[70, 130, 530, 150]),
        make_item(
            item_id="eq", item_type="equation", text="r = 1",
            order=2, page=1,
            content={"latex": "r = 1", "label": "(2)", "raw_text": "r = 1"},
        ),
        make_item(
            item_id="tbl", item_type="table", text=table_markdown, order=3, page=1,
            content={
                "label": "Table 9", "n_rows": 2, "n_cols": 2, "cells": cells,
                "markdown": table_markdown,
            },
        ),
        make_item(
            item_id="fig", item_type="figure", text="", order=4, page=1,
            content={"label": "Figure 2", "asset_path": "assets/figures/fig_0005.png"},
        ),
        make_item(item_id="cap", item_type="caption", text="Figure 2. Sample caption.", order=5, page=1, bbox=[70, 200, 530, 220]),
    ]
    _, _, _, result = run_parse(project_tmp_path, items, monkeypatch=monkeypatch, page_count=1)
    artifacts = read_artifacts(l2_config, result.paper_id, result.parse_run_id)
    md = artifacts["markdown"]

    assert "A plain paragraph." in md
    assert "- first item" in md
    assert "$$\nr = 1\n$$\n(2)" in md
    assert "| Method | Wait |" in md
    assert "| DRL | 5.1 |" in md
    assert "![Figure 2](assets/figures/fig_0005.png)" in md
    assert "Figure 2. Sample caption." in md


def test_markdown_map_full_gapless_coverage(project_tmp_path, monkeypatch, l2_config):
    """AC-L2S1-MARKDOWN-03: every paper.md line is covered by exactly one map
    record and every record's range lies within the file."""
    _, _, _, result = run_parse(
        project_tmp_path, canonical_fixture_items(), monkeypatch=monkeypatch, page_count=1
    )
    artifacts = read_artifacts(l2_config, result.paper_id, result.parse_run_id)
    md_lines = artifacts["markdown"].split("\n")
    entries = [
        json.loads(line) for line in artifacts["markdown_map"].splitlines() if line.strip()
    ]
    assert entries
    total_lines = len(md_lines)

    covered = 0
    for entry in entries:
        start, end = entry["md_line_start"], entry["md_line_end"]
        assert 1 <= start <= end <= total_lines
        covered += end - start + 1
    assert covered == total_lines, "lines not fully covered by exactly one entry"

    # non-overlapping, gapless
    ordered = sorted(entries, key=lambda e: e["md_line_start"])
    for prev, curr in zip(ordered, ordered[1:]):
        assert curr["md_line_start"] == prev["md_line_end"] + 1
        assert curr["md_line_start"] > prev["md_line_end"]


def test_markdown_grep_resolves_to_provenance(project_tmp_path, monkeypatch, l2_config):
    """AC-L2S1-MARKDOWN-04: a grep hit on a markdown line resolves through the
    map to its block_id, then to block provenance, then to PDF page/bbox."""
    items = [
        make_item(item_id="h1", item_type="heading", text="Intro", order=0, page=1, level=1, bbox=[70, 100, 530, 120]),
        make_item(
            item_id="p1", item_type="paragraph",
            text="The unique phrase SNC-42 appears in the body.",
            order=1, page=2, bbox=[70.0, 300.0, 530.0, 320.0],
        ),
    ]
    _, _, _, result = run_parse(project_tmp_path, items, monkeypatch=monkeypatch, page_count=2)
    from transit_scholar.layer2 import grep_paper, read_blocks

    greps = grep_paper(result.paper_id, "SNC-42", config=l2_config)
    assert greps.status == "ok"
    assert len(greps.hits) == 1
    hit = greps.hits[0]
    assert "SNC-42" in hit.text

    # hit -> source_refs -> canonical block -> provenance -> page/bbox
    block_ids = [ref.block_id for ref in hit.source_refs]
    blocks = read_blocks(result.paper_id, block_ids, config=l2_config)
    assert len(blocks) == 1
    block = blocks[0]
    assert "SNC-42" in block["text"]
    assert block["provenance"][0]["page"] == 2
    assert block["provenance"][0]["bbox"] == [70.0, 300.0, 530.0, 320.0]


def test_markdown_rerender_keeps_canonical_unchanged(
    project_tmp_path, monkeypatch, l2_config
):
    """AC-L2S1-MARKDOWN-05: re-rendering with a different renderer version does
    not reparse the PDF; canonical files stay byte-identical and only
    markdown/map are regenerated."""
    import copy

    _, _, _, result = run_parse(
        project_tmp_path, canonical_fixture_items(), monkeypatch=monkeypatch, page_count=1
    )
    artifacts_before = read_artifacts(l2_config, result.paper_id, result.parse_run_id)
    canonical_before = (
        artifacts_before["document"].to_dict(),
        [s.to_dict() for s in artifacts_before["sections"]],
        [b.to_dict() for b in artifacts_before["blocks"]],
    )
    import json as _json

    canonical_before_bytes = _json.dumps(canonical_before, sort_keys=True, default=str)
    run_dir = artifacts_before["run_dir"]

    from transit_scholar.layer2.pipeline import rebuild_derived

    config_v2 = copy.copy(l2_config)
    object.__setattr__(config_v2, "renderer_version", "2.0")
    outcome = rebuild_derived(result.paper_id, config=config_v2)
    assert outcome["status"] == "ok"
    assert outcome["parse_run_id"] == result.parse_run_id

    artifacts_after = read_artifacts(l2_config, result.paper_id, result.parse_run_id)
    canonical_after = (
        artifacts_after["document"].to_dict(),
        [s.to_dict() for s in artifacts_after["sections"]],
        [b.to_dict() for b in artifacts_after["blocks"]],
    )
    canonical_after_bytes = _json.dumps(canonical_after, sort_keys=True, default=str)
    assert canonical_after_bytes == canonical_before_bytes

    assert artifacts_after["markdown"] == artifacts_before["markdown"]
    assert "renderer_version" in artifacts_after["markdown_map"]
    for line in artifacts_after["markdown_map"].splitlines():
        assert json.loads(line)["renderer_version"] == "2.0"

    manifest = json.loads((run_dir / "parser_manifest.json").read_text(encoding="utf-8"))
    assert manifest["renderer_version"] == "2.0"
