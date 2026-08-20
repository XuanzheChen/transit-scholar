"""Layer2 Step1 normalizer tests (AC-L2S1-NORMALIZER-01..06)."""

from __future__ import annotations

from transit_scholar.layer2.normalizer import Normalizer, clean_text
from transit_scholar.layer2.parser.fake import FakeParserAdapter, make_item
from tests.l2s1_fixtures import (
    canonical_fixture_items,
    cross_page_paragraph_items,
    read_artifacts,
    run_parse,
)

BODY_BBOX = [70.0, 100.0, 530.0, 120.0]


def _normalize(l2_config, items, *, page_count=1, created_at=None):
    result = FakeParserAdapter(items=items, page_count=page_count).parse("ignored.pdf")
    normalizer = Normalizer(l2_config)
    return normalizer.normalize(
        result,
        paper_id="paper_x",
        file_id="file_x",
        source_sha256="sha",
        parse_run_id="run_1",
        created_at=created_at,
    )


def test_normalizer_deterministic(project_tmp_path, monkeypatch, l2_config):
    """AC-L2S1-NORMALIZER-01: identical parser input plus the same explicit
    parse-run context (created_at) yields byte-identical canonical output
    across repeated invocations."""
    items = canonical_fixture_items()
    created_at = "2026-08-12T00:00:00+00:00"
    out_a = _normalize(l2_config, items, created_at=created_at)
    out_b = _normalize(l2_config, items, created_at=created_at)

    from transit_scholar.layer2.schema import CanonicalBlock, CanonicalDocument

    def serialize(out):
        return (
            CanonicalDocument.to_dict(out.document),
            [s.to_dict() for s in out.sections],
            [b.to_dict() for b in out.blocks],
        )

    import json

    assert json.dumps(serialize(out_a), sort_keys=True, default=str) == json.dumps(
        serialize(out_b), sort_keys=True, default=str
    )


def test_normalizer_created_at_from_parse_run_context(l2_config):
    """AC-L2S1-NORMALIZER-01: canonical created_at is stable for an identical
    parse-run context — a parse_<ts>-<hex> run id yields the same derived
    timestamp across invocations, and an explicit created_at overrides it."""
    result = FakeParserAdapter(
        items=[make_item(
            item_id="p1", item_type="paragraph",
            text="A deterministic paragraph.", order=0, page=1, bbox=BODY_BBOX,
        )],
        page_count=1,
    ).parse("ignored.pdf")
    run_id = "parse_20260812T120000Z-abcdef1234"
    normalizer = Normalizer(l2_config)

    out_a = normalizer.normalize(
        result, paper_id="p", file_id="f", source_sha256="s", parse_run_id=run_id
    )
    out_b = normalizer.normalize(
        result, paper_id="p", file_id="f", source_sha256="s", parse_run_id=run_id
    )
    assert out_a.document.created_at == "2026-08-12T12:00:00+00:00"
    assert out_a.document.created_at == out_b.document.created_at

    explicit = normalizer.normalize(
        result,
        paper_id="p", file_id="f", source_sha256="s", parse_run_id=run_id,
        created_at="2030-01-01T00:00:00+00:00",
    )
    assert explicit.document.created_at == "2030-01-01T00:00:00+00:00"


def test_normalizer_reading_order_stable_and_renumbered(
    project_tmp_path, monkeypatch, l2_config
):
    """AC-L2S1-NORMALIZER-02: canonical order is document-wide, monotonic,
    renumbered and independent of parser item order."""
    items = [
        make_item(item_id="b", item_type="paragraph", text="Second paragraph.", order=5, page=1, bbox=BODY_BBOX),
        make_item(item_id="a", item_type="paragraph", text="First paragraph.", order=1, page=1, bbox=BODY_BBOX),
    ]
    out = _normalize(l2_config, items)
    orders = [b.order for b in out.blocks]
    assert orders == sorted(orders)
    assert len(set(orders)) == len(orders)
    assert orders == [1, 2]
    assert [b.text for b in out.blocks] == ["First paragraph.", "Second paragraph."]


def test_normalizer_heading_creates_section_anchor(project_tmp_path, monkeypatch, l2_config):
    """AC-L2S1-NORMALIZER-03: a heading becomes a heading block and a section
    whose heading_block_id points back; content stored once."""
    items = [
        make_item(item_id="h1", item_type="heading", text="1 Introduction", order=0, page=1, level=1, bbox=BODY_BBOX),
        make_item(item_id="p1", item_type="paragraph", text="body text", order=1, page=1, bbox=BODY_BBOX),
        make_item(item_id="h2", item_type="heading", text="1.1 Motivation", order=2, page=1, level=2, bbox=BODY_BBOX),
        make_item(item_id="p2", item_type="paragraph", text="more text", order=3, page=1, bbox=BODY_BBOX),
    ]
    out = _normalize(l2_config, items)
    assert len(out.sections) == 2
    heading_blocks = [b for b in out.blocks if b.block_type == "heading"]
    assert len(heading_blocks) == 2

    sec1, sec2 = out.sections
    assert sec1.level == 1
    assert sec1.parent_section_id is None
    assert sec1.heading_block_id == heading_blocks[0].block_id
    assert sec1.title == "1 Introduction"

    assert sec2.level == 2
    assert sec2.parent_section_id == sec1.section_id
    assert sec2.heading_block_id == heading_blocks[1].block_id

    # heading content stored once: the block has the text; the section reuses it
    assert heading_blocks[0].text == "1 Introduction"
    assert sec1.title == heading_blocks[0].text

    # paragraph blocks bound to their section
    para_blocks = [b for b in out.blocks if b.block_type == "paragraph"]
    assert para_blocks[0].section_id == sec1.section_id
    assert para_blocks[1].section_id == sec2.section_id


def test_normalizer_per_type_blocks_and_char_ranges(project_tmp_path, monkeypatch, l2_config):
    """AC-L2S1-NORMALIZER-04: per-type block counts correct and each provenance
    char range slices the block text."""
    items = canonical_fixture_items()
    out = _normalize(l2_config, items)
    types = [b.block_type for b in out.blocks]
    assert types.count("paragraph") == 3
    assert types.count("heading") == 2
    assert types.count("equation") == 1

    for block in out.blocks:
        for prov in block.provenance:
            if prov.char_start == prov.char_end and not block.text:
                continue
            assert prov.char_start <= prov.char_end <= len(block.text)
            assert block.text[prov.char_start : prov.char_end] != ""


def test_normalizer_text_cleaning_and_hyphenation(l2_config):
    """AC-L2S1-NORMALIZER-05: cleaning removes control chars / abnormal
    whitespace and fixes line-end hyphenation without altering punctuation."""
    dirty = "reinforce-\nment learning\t\tmethods\x00\x01are\x0b\tsuperior"
    cleaned = clean_text(dirty)
    assert cleaned == "reinforcement learning methods are superior"

    # hyphenation fix keeps meaningful punctuation
    text = "The reward function balances wait-\ning time, headway, and cost."
    assert clean_text(text) == "The reward function balances waiting time, headway, and cost."

    # provenance char ranges recomputed against cleaned text
    items = [
        make_item(
            item_id="p1", item_type="paragraph",
            text="reinforce-\nment learning",
            order=0, page=1, bbox=BODY_BBOX,
        )
    ]
    out = _normalize(l2_config, items)
    block = out.blocks[0]
    assert block.text == "reinforcement learning"
    assert block.provenance[0].char_start == 0
    assert block.provenance[0].char_end == len(block.text)


def test_normalizer_page_zero_keeps_text_but_no_provenance(l2_config):
    """Canonical provenance semantics: a parser item with ``page <= 0`` means
    "no page-level source". The block keeps text + source_items but must NOT
    carry a fabricated ``CanonicalProvenance(page=0)`` / pages list."""
    items = [
        make_item(
            item_id="h1", item_type="heading", text="Introduction",
            order=0, page=0, level=1,
        ),
        make_item(
            item_id="p1", item_type="paragraph",
            text="Readable body without page-level provenance.",
            order=1, page=0,
        ),
        make_item(
            item_id="p2", item_type="paragraph",
            text="Second body segment.",
            order=2, page=0,
        ),
    ]
    out = _normalize(l2_config, items, page_count=1)

    for block in out.blocks:
        assert block.pages == []
        assert block.provenance == []
        assert block.source_items, "source_items must be preserved without provenance"

    body_blocks = [b for b in out.blocks if b.block_type == "paragraph"]
    assert len(body_blocks) == 2
    assert body_blocks[0].text == "Readable body without page-level provenance."
    assert body_blocks[0].source_items == ["p1"]
    assert body_blocks[1].source_items == ["p2"]
    assert out.document.page_count == 1  # parser-declared page count kept


def test_normalizer_page_zero_type_blocks_no_crash(l2_config):
    """Table/equation/figure items without page provenance still produce
    valid blocks (char_end guards, no IndexError on empty provenance)."""
    markdown = "| Method | Value |\n| --- | --- |\n| DRL | 5.1 |"
    items = [
        make_item(
            item_id="tbl0", item_type="table", text=markdown, order=0, page=0,
            content={
                "label": "Table 0", "n_rows": 2, "n_cols": 2,
                "cells": [], "markdown": markdown,
            },
        ),
        make_item(
            item_id="eq0", item_type="equation",
            text="r_t = -w * wait_t", order=1, page=0,
            content={"latex": "r_t = -w \\cdot wait_t", "label": "", "raw_text": "r_t = -w * wait_t"},
        ),
        make_item(
            item_id="fig0", item_type="figure", text="", order=2, page=0,
            content={"label": "Figure 0", "asset_path": ""},
        ),
    ]
    out = _normalize(l2_config, items, page_count=1)

    types = {b.block_type: b for b in out.blocks}
    assert types["table"].text == markdown
    assert types["table"].provenance == []
    assert types["table"].content["markdown"] == markdown
    assert types["equation"].text == "r_t = -w \\cdot wait_t"
    assert types["equation"].provenance == []
    assert types["figure"].text == ""
    assert types["figure"].provenance == []
    assert all(b.pages == [] for b in out.blocks)


def test_normalizer_mixed_pages_and_no_page_provenance(l2_config):
    """Merging a page-true item with a page-0 continuation item keeps the
    real provenance segment and never fabricates a page-0 segment."""
    items = [
        make_item(
            item_id="p1", item_type="paragraph",
            text="The method controls the buses according to",
            order=0, page=1, bbox=BODY_BBOX, font_size=10.0,
        ),
        make_item(
            item_id="p2", item_type="paragraph",
            text="the forward and backward headways",
            order=1, page=0, font_size=10.0,
        ),
    ]
    out = _normalize(l2_config, items, page_count=1)
    paragraphs = [b for b in out.blocks if b.block_type == "paragraph"]
    assert len(paragraphs) == 1
    block = paragraphs[0]
    assert block.pages == [1]
    assert len(block.provenance) == 1
    assert block.provenance[0].page == 1
    assert block.source_items == ["p1", "p2"]
    assert block.text.endswith("the forward and backward headways")


def test_normalizer_merge_matrix(l2_config):
    """AC-L2S1-NORMALIZER-06: paragraph reconstruction resolves each explicit
    pass/fail merge fixture per the conservative rules."""
    normalizer = Normalizer(l2_config)
    body = [70.0, 100.0, 530.0, 130.0]
    indented = [90.0, 140.0, 530.0, 160.0]

    def para(item_id, text, order, page=1, bbox=body, font=10.0):
        return make_item(
            item_id=item_id, item_type="paragraph", text=text, order=order,
            page=page, bbox=list(bbox), font_size=font,
        )

    # same-page mid-sentence continuation -> merge
    assert normalizer.should_merge(
        para("a", "The method controls the buses according to", 0),
        para("b", "the forward and backward headways", 1),
    )

    # cross-page mid-sentence continuation -> merge (strong signal)
    a = para("a", "The reward function balances passenger", 0, page=1,
             bbox=[70.0, 650.0, 530.0, 665.0])
    b = para("b", "waiting time and bus regularity", 1, page=2,
             bbox=[70.0, 60.0, 530.0, 75.0])
    assert normalizer.should_merge(a, b, page_heights={1: 800.0, 2: 800.0})

    # hyphen continuation -> merge
    assert normalizer.should_merge(
        para("a", "The system learns to optimi-", 0),
        para("b", "zation of headways", 1),
    )

    # complete sentence + new-paragraph indent -> do NOT merge
    assert not normalizer.should_merge(
        para("a", "This paragraph is complete.", 0),
        para("b", "A new indented paragraph starts.", 1, bbox=indented),
    )

    # complete sentence, same margin, conservative default -> do NOT merge
    assert not normalizer.should_merge(
        para("a", "This paragraph ends with a period.", 0),
        para("b", "The next paragraph continues.", 1),
    )

    # heading / table / caption / list boundaries -> never merge (not both paragraph)
    assert not normalizer.should_merge(
        para("a", "Some text", 0),
        make_item(item_id="h", item_type="heading", text="Heading", order=1, page=1, level=1, bbox=BODY_BBOX),
    )
    assert not normalizer.should_merge(
        make_item(item_id="t", item_type="table", text="| a |", order=0, page=1, bbox=BODY_BBOX),
        para("b", "text after table", 1),
    )
    assert not normalizer.should_merge(
        para("a", "text before caption", 0),
        make_item(item_id="c", item_type="caption", text="Figure 1. x", order=1, page=1, bbox=BODY_BBOX),
    )

    # font size mismatch -> do NOT merge
    assert not normalizer.should_merge(
        para("a", "normal sized text", 0),
        para("b", "different size text", 1, font=16.0),
    )

    # column / geometry mismatch -> do NOT merge
    assert not normalizer.should_merge(
        para("a", "left column text", 0, bbox=[40.0, 100.0, 250.0, 120.0]),
        para("b", "right column text", 1, bbox=[300.0, 100.0, 520.0, 120.0]),
    )


def test_normalizer_cross_page_merge_output(project_tmp_path, monkeypatch, l2_config):
    """AC-L2S1-NORMALIZER-06: cross-page paragraph with strong continuation is
    merged into a single block with two provenance segments."""
    out = _normalize(l2_config, cross_page_paragraph_items())
    paragraphs = [b for b in out.blocks if b.block_type == "paragraph"]
    assert len(paragraphs) == 1
    block = paragraphs[0]
    assert block.pages == [1, 2]
    assert len(block.provenance) == 2
    assert block.text == "The reward function is designed to balance passenger waiting time and bus regularity."
