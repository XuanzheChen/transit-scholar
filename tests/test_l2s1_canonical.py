"""Layer2 Step1 canonical model tests (AC-L2S1-CANONICAL-01..04)."""

from __future__ import annotations

import json

import pytest

from tests.l2s1_fixtures import (
    canonical_fixture_items,
    cross_page_paragraph_items,
    read_artifacts,
    run_parse,
)

DOCUMENT_FIELDS = {
    "paper_id",
    "file_id",
    "source_sha256",
    "parse_run_id",
    "parser_name",
    "parser_version",
    "parser_config_hash",
    "canonical_schema_version",
    "normalizer_version",
    "page_count",
    "language",
    "section_count",
    "block_count",
    "parse_status",
    "created_at",
}

SECTION_FIELDS = {
    "section_id",
    "paper_id",
    "title",
    "level",
    "parent_section_id",
    "order",
    "heading_block_id",
}

BLOCK_FIELDS = {
    "block_id",
    "paper_id",
    "block_type",
    "section_id",
    "order",
    "text",
    "pages",
    "provenance",
    "source_items",
    "relations",
    "content",
}

PROVENANCE_FIELDS = {"page", "bbox", "source_item_id", "char_start", "char_end"}

FORBIDDEN_TERMS = ("reward_function", "state_definition", "action_space", "baseline")


def _make_run(project_tmp_path, monkeypatch, items, page_count=1):
    _, _, _, result = run_parse(
        project_tmp_path, items, monkeypatch=monkeypatch, page_count=page_count
    )
    assert result.status in ("passed", "degraded")
    return result


def test_canonical_document_full_key_set_and_counts(
    project_tmp_path, monkeypatch, l2_config
):
    """AC-L2S1-CANONICAL-01: document.json carries exactly the required fields
    and section_count/block_count match the actual files."""
    result = _make_run(
        project_tmp_path, monkeypatch, canonical_fixture_items()
    )
    artifacts = read_artifacts(l2_config, result.paper_id, result.parse_run_id)
    document = artifacts["document"]
    assert set(document.to_dict().keys()) == DOCUMENT_FIELDS
    assert document.section_count == len(artifacts["sections"])
    assert document.block_count == len(artifacts["blocks"])
    assert document.parse_status == "passed"
    assert document.paper_id == result.paper_id


def test_canonical_sections_and_blocks_schema(project_tmp_path, monkeypatch, l2_config):
    """AC-L2S1-CANONICAL-02: required keys/types present; a fixture with a
    missing key is rejected by validation."""
    result = _make_run(project_tmp_path, monkeypatch, canonical_fixture_items())
    artifacts = read_artifacts(l2_config, result.paper_id, result.parse_run_id)

    for section in artifacts["sections"]:
        assert set(section.to_dict().keys()) == SECTION_FIELDS
    for block in artifacts["blocks"]:
        assert set(block.to_dict().keys()) == BLOCK_FIELDS
        for prov in block.provenance:
            assert set(prov.to_dict().keys()) == PROVENANCE_FIELDS
            assert prov.page >= 1
            assert isinstance(prov.char_start, int)
            assert isinstance(prov.char_end, int)

    # negative: missing key must raise
    from transit_scholar.layer2.schema import CanonicalBlock

    record = artifacts["blocks"][0].to_dict()
    del record["block_type"]
    with pytest.raises(ValueError):
        CanonicalBlock.from_dict(record)


def test_canonical_block_types_restricted_and_forbidden_absent(
    project_tmp_path, monkeypatch, l2_config
):
    """AC-L2S1-CANONICAL-03: block types stay in the allowed set and no
    domain-semantic term appears in any generated artifact."""
    result = _make_run(project_tmp_path, monkeypatch, canonical_fixture_items())
    artifacts = read_artifacts(l2_config, result.paper_id, result.parse_run_id)

    from transit_scholar.layer2.schema import BLOCK_TYPES

    for block in artifacts["blocks"]:
        assert block.block_type in BLOCK_TYPES

    run_dir = artifacts["run_dir"]
    blob = ""
    for name in (
        "document.json",
        "sections.json",
        "blocks.jsonl",
        "parser_manifest.json",
        "paper.md",
        "markdown_map.jsonl",
        "retrieval_chunks.jsonl",
    ):
        blob += (run_dir / name).read_text(encoding="utf-8")
    for term in FORBIDDEN_TERMS:
        assert term not in blob


def test_canonical_block_type_field_validation_rejects_domain_type():
    """AC-L2S1-CANONICAL-03: CanonicalBlock.from_dict rejects a forbidden type."""
    from transit_scholar.layer2.schema import CanonicalBlock

    with pytest.raises(ValueError):
        CanonicalBlock.from_dict(
            {
                "block_id": "blk_00001",
                "paper_id": "p",
                "block_type": "reward_function",
                "section_id": None,
                "order": 1,
                "text": "x",
            }
        )


def test_canonical_cross_page_provenance(project_tmp_path, monkeypatch, l2_config):
    """AC-L2S1-CANONICAL-04: a merged cross-page paragraph carries >=2
    provenance segments with pages == sorted distinct pages and monotonic
    contiguous char ranges."""
    result = _make_run(project_tmp_path, monkeypatch, cross_page_paragraph_items(), page_count=2)
    artifacts = read_artifacts(l2_config, result.paper_id, result.parse_run_id)
    paragraphs = [b for b in artifacts["blocks"] if b.block_type == "paragraph"]
    assert len(paragraphs) == 1
    block = paragraphs[0]
    assert len(block.provenance) >= 2
    assert block.pages == sorted({p.page for p in block.provenance})
    previous_end = 0
    for prov in block.provenance:
        assert prov.char_start == previous_end
        assert prov.char_end > prov.char_start
        assert block.text[prov.char_start : prov.char_end] != ""
        previous_end = prov.char_end
    assert previous_end == len(block.text)
