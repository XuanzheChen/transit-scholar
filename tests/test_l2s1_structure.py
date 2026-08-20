"""Layer2 Step1 structure tests (AC-L2S1-STRUCT-01..04)."""

from __future__ import annotations

from tests.l2s1_fixtures import (
    equation_items,
    read_artifacts,
    run_parse,
    table_caption_items,
)


def test_table_content_structure(project_tmp_path, monkeypatch, l2_config):
    """AC-L2S1-STRUCT-01: table content carries label/n_rows/n_cols/cells/
    markdown and the grid dims match the cells."""
    _, _, _, result = run_parse(
        project_tmp_path, table_caption_items(), monkeypatch=monkeypatch, page_count=3
    )
    artifacts = read_artifacts(l2_config, result.paper_id, result.parse_run_id)
    table = [b for b in artifacts["blocks"] if b.block_type == "table"][0]
    content = table.content
    assert set(content.keys()) == {"label", "n_rows", "n_cols", "cells", "markdown"}
    assert content["label"] == "Table 1"
    assert content["n_rows"] == 2
    assert content["n_cols"] == 2
    max_row = max(cell["row"] for cell in content["cells"])
    max_col = max(cell["col"] for cell in content["cells"])
    assert content["n_rows"] == max_row + 1
    assert content["n_cols"] == max_col + 1
    for cell in content["cells"]:
        assert set(cell.keys()) == {
            "row", "col", "row_span", "col_span", "text", "is_header",
        }
    assert "| Method | Mean Wait |" in content["markdown"]
    assert "| DRL | 5.1 |" in content["markdown"]


def test_equation_content_structure(project_tmp_path, monkeypatch, l2_config):
    """AC-L2S1-STRUCT-02: equation content carries latex/label/raw_text and the
    latex is preserved verbatim (no LLM rewrite)."""
    _, _, _, result = run_parse(
        project_tmp_path, equation_items(), monkeypatch=monkeypatch, page_count=1
    )
    artifacts = read_artifacts(l2_config, result.paper_id, result.parse_run_id)
    equation = [b for b in artifacts["blocks"] if b.block_type == "equation"][0]
    content = equation.content
    assert set(content.keys()) == {"latex", "label", "raw_text"}
    assert content["latex"] == "J = \\mathbb{E}[\\sum_t \\gamma^t r_t]"
    assert content["label"] == "(7)"
    assert content["raw_text"] == "J = E[sum gamma^t r_t]"
    assert equation.text == content["latex"]


def test_figure_content_and_asset(project_tmp_path, monkeypatch, l2_config):
    """AC-L2S1-STRUCT-03: figure content carries label and asset_path pointing
    into assets/figures/; the file exists in the run dir; no description field."""
    _, _, _, result = run_parse(
        project_tmp_path, table_caption_items(), monkeypatch=monkeypatch, page_count=3
    )
    artifacts = read_artifacts(l2_config, result.paper_id, result.parse_run_id)
    figure = [b for b in artifacts["blocks"] if b.block_type == "figure"][0]
    content = figure.content
    assert "label" in content
    assert content["label"] == "Figure 1"
    assert "description" not in content
    asset_path = content["asset_path"]
    assert asset_path.startswith("assets/figures/")
    asset_file = artifacts["run_dir"] / asset_path
    assert asset_file.is_file()
    assert asset_file.read_bytes() == b"\x89PNG fake image bytes"


def test_caption_bidirectional_relation(project_tmp_path, monkeypatch, l2_config):
    """AC-L2S1-STRUCT-04: captions are independent blocks with their own
    provenance and bidirectional parent<->caption consistency."""
    _, _, _, result = run_parse(
        project_tmp_path, table_caption_items(), monkeypatch=monkeypatch, page_count=3
    )
    artifacts = read_artifacts(l2_config, result.paper_id, result.parse_run_id)
    blocks = {b.block_id: b for b in artifacts["blocks"]}

    captions = [b for b in artifacts["blocks"] if b.block_type == "caption"]
    assert len(captions) == 2
    for caption in captions:
        assert caption.text
        assert caption.pages
        assert caption.provenance
        parent_id = caption.relations["parent_block_id"]
        parent = blocks[parent_id]
        assert parent.block_type in ("table", "figure", "equation")
        assert caption.block_id in parent.relations.get("caption_block_ids", [])
        # consistency both directions
        for linked in parent.relations["caption_block_ids"]:
            assert blocks[linked].relations["parent_block_id"] == parent_id

    table = [b for b in artifacts["blocks"] if b.block_type == "table"][0]
    figure = [b for b in artifacts["blocks"] if b.block_type == "figure"][0]
    assert len(table.relations["caption_block_ids"]) == 1
    assert len(figure.relations["caption_block_ids"]) == 1
