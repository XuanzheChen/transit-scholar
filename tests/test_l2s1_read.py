"""Layer2 Step1 read API tests (AC-L2S1-READ-01..03)."""

from __future__ import annotations

import json

from transit_scholar.layer2.paths import load_current, run_paths
from tests.l2s1_fixtures import (
    canonical_fixture_items,
    read_artifacts,
    run_parse,
)


def _make_run(project_tmp_path, monkeypatch, l2_config):
    _, _, _, result = run_parse(
        project_tmp_path, canonical_fixture_items(), monkeypatch=monkeypatch, page_count=1
    )
    return result


def test_read_blocks_returns_canonical(project_tmp_path, monkeypatch, l2_config):
    """AC-L2S1-READ-01: read_blocks returns the canonical blocks for the
    requested ids, matching blocks.jsonl."""
    result = _make_run(project_tmp_path, monkeypatch, l2_config)
    artifacts = read_artifacts(l2_config, result.paper_id, result.parse_run_id)
    expected = {b.block_id: b.to_dict() for b in artifacts["blocks"]}

    from transit_scholar.layer2 import read_blocks

    requested = list(expected.keys())[:2]
    returned = read_blocks(result.paper_id, requested, config=l2_config)
    assert [b["block_id"] for b in returned] == requested
    for block in returned:
        assert block == expected[block["block_id"]]


def test_read_context_uses_reading_order(project_tmp_path, monkeypatch, l2_config):
    """AC-L2S1-READ-02: read_context returns canonical-order neighbours clamped
    at edges, never fabricated via chunk overlap."""
    result = _make_run(project_tmp_path, monkeypatch, l2_config)
    artifacts = read_artifacts(l2_config, result.paper_id, result.parse_run_id)
    blocks = sorted(artifacts["blocks"], key=lambda b: b.order)
    ids = [b.block_id for b in blocks]

    from transit_scholar.layer2 import read_context

    # middle block: before=2 after=2
    middle = ids[3]
    context = read_context(result.paper_id, middle, config=l2_config)
    ctx_ids = [b["block_id"] for b in context]
    assert ctx_ids == ids[max(0, 3 - 2): 3 + 2 + 1]

    # edge block 0: no negative context
    edge = read_context(result.paper_id, ids[0], config=l2_config)
    assert [b["block_id"] for b in edge] == ids[:3]

    # unknown block -> empty
    assert read_context(result.paper_id, "blk_nope", config=l2_config) == []

    # context comes from canonical order, not chunk text: the neighbour set is
    # exactly the reading-order neighbours
    assert set(ctx_ids) == set(ids[1:6])


def test_read_section_returns_section_blocks(project_tmp_path, monkeypatch, l2_config):
    """AC-L2S1-READ-03: read_section returns exactly the blocks of a section in
    reading order."""
    result = _make_run(project_tmp_path, monkeypatch, l2_config)
    current = load_current(l2_config.parsed_paper_dir(result.paper_id))
    rp = run_paths(l2_config, result.paper_id, current)
    sections = json.loads(rp.sections_path.read_text(encoding="utf-8"))
    assert sections

    from transit_scholar.layer2 import read_section
    from transit_scholar.layer2.retrieval.api import _load_blocks

    first_section = sections[0]["section_id"]
    returned = read_section(result.paper_id, first_section, config=l2_config)

    rp_blocks = _load_blocks(rp)
    expected_ids = sorted(
        (b.block_id for b in rp_blocks if b.section_id == first_section),
        key=lambda bid: next(b.order for b in rp_blocks if b.block_id == bid),
    )
    assert [b["block_id"] for b in returned] == expected_ids
    for block in returned:
        assert block["section_id"] == first_section
