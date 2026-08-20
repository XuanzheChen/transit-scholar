"""Layer2 Step1 validation & fallback tests (AC-L2S1-VALIDATION-01..05)."""

from __future__ import annotations

from transit_scholar.layer2.normalizer import Normalizer
from transit_scholar.layer2.parser.fake import FakeParserAdapter, make_item
from transit_scholar.layer2.schema import CanonicalBlock, CanonicalDocument
from transit_scholar.layer2.validation import ParseValidator
from tests.l2s1_fixtures import read_artifacts, run_parse

BODY_BBOX = [70.0, 100.0, 530.0, 120.0]


def _parser_result(items, page_count=None, status="ok", error_code=None, error_message=None):
    return FakeParserAdapter(
        items=items, page_count=page_count, status=status,
        error_code=error_code, error_message=error_message,
    ).parse("ignored.pdf")


def _normalized(l2_config, items, page_count=None):
    result = _parser_result(items, page_count=page_count)
    return Normalizer(l2_config).normalize(
        result,
        paper_id="p", file_id="f", source_sha256="sha", parse_run_id="run",
    )


def _validate(l2_config, items, page_count=None):
    result = _parser_result(items, page_count=page_count)
    out = Normalizer(l2_config).normalize(
        result,
        paper_id="p", file_id="f", source_sha256="sha", parse_run_id="run",
    )
    return ParseValidator(l2_config).validate(
        result, out.document, out.sections, out.blocks
    )


def test_validation_status_vocabulary(project_tmp_path, monkeypatch, l2_config):
    """AC-L2S1-VALIDATION-01: exactly one of passed/degraded/failed/needs_review."""
    items = [
        make_item(item_id="p1", item_type="paragraph", text="Some body text.", order=0, page=1, bbox=BODY_BBOX)
    ]
    passed = _validate(l2_config, items, page_count=1)
    assert passed.status in ("passed", "degraded", "failed", "needs_review")
    assert passed.signals


def test_validation_hard_failures(project_tmp_path, monkeypatch, l2_config):
    """AC-L2S1-VALIDATION-02: hard failure matrix -> failed with error_code."""
    items = [
        make_item(item_id="p1", item_type="paragraph", text="Some body text.", order=0, page=1, bbox=BODY_BBOX)
    ]

    # 1. parser exception
    result = _parser_result(items, page_count=1, status="error", error_code="PDF_OPEN_FAILED")
    out = _normalized(l2_config, items, page_count=1)
    validation = ParseValidator(l2_config).validate(result, out.document, out.sections, out.blocks)
    assert validation.status == "failed"
    assert validation.error_code == "PDF_OPEN_FAILED"

    # 2. no canonical document
    ok_result = _parser_result(items, page_count=1)
    validation = ParseValidator(l2_config).validate(ok_result, None, [], [])
    assert validation.status == "failed"
    assert validation.error_code == "NO_CANONICAL_DOCUMENT"

    # 3. page_count mismatch
    result = _parser_result(items, page_count=5)
    out = _normalized(l2_config, items, page_count=5)
    out.document.page_count = 99
    validation = ParseValidator(l2_config).validate(result, out.document, out.sections, out.blocks)
    assert validation.status == "failed"
    assert validation.error_code == "PARSE_VALIDATION_FAILED"
    assert any(not s.ok and s.name == "page_count" for s in validation.signals)

    # 4. near-empty body
    validation = _validate(l2_config, [], page_count=5)
    assert validation.status == "failed"
    assert any(not s.ok and s.name == "near_empty_body" for s in validation.signals)

    # 5. reading order unbuildable
    two_blocks = [
        make_item(item_id="p1", item_type="paragraph", text="First body text.", order=0, page=1, bbox=BODY_BBOX),
        make_item(item_id="p2", item_type="paragraph", text="Second body text.", order=1, page=1, bbox=BODY_BBOX),
    ]
    result = _parser_result(two_blocks, page_count=1)
    out = _normalized(l2_config, two_blocks, page_count=1)
    out.blocks[0].order = 2
    out.blocks[1].order = 1
    validation = ParseValidator(l2_config).validate(result, out.document, out.sections, out.blocks)
    assert validation.status == "failed"
    assert any(not s.ok and s.name == "reading_order" for s in validation.signals)

    # 6. illegal provenance page
    result = _parser_result(items, page_count=1)
    out = _normalized(l2_config, items, page_count=1)
    out.blocks[0].provenance[0].page = 99
    validation = ParseValidator(l2_config).validate(result, out.document, out.sections, out.blocks)
    assert validation.status == "failed"
    assert any(not s.ok and s.name == "provenance_legal" for s in validation.signals)

    # 7. corrupted canonical structure (section heading not linked)
    structured_items = [
        make_item(item_id="h", item_type="heading", text="Intro", order=0, page=1, level=1, bbox=BODY_BBOX),
        make_item(item_id="p", item_type="paragraph", text="Body text.", order=1, page=1, bbox=BODY_BBOX),
    ]
    result = _parser_result(structured_items, page_count=1)
    out = _normalized(l2_config, structured_items, page_count=1)
    out.sections[0].heading_block_id = "blk_does_not_exist"
    validation = ParseValidator(l2_config).validate(result, out.document, out.sections, out.blocks)
    assert validation.status == "failed"
    assert any(not s.ok and s.name == "structure_valid" for s in validation.signals)


def test_validation_degraded_signals(project_tmp_path, monkeypatch, l2_config):
    """AC-L2S1-VALIDATION-03: each degraded signal crosses its threshold and
    yields degraded (not failed)."""
    # meaningful text page ratio < 0.80
    sparse = [
        make_item(item_id=f"p{i}", item_type="paragraph", text=f"text {i}", order=i, page=1, bbox=BODY_BBOX)
        for i in range(3)
    ]
    validation = _validate(l2_config, sparse, page_count=10)
    assert validation.status == "degraded"
    assert any(not s.ok and s.name == "meaningful_text_page_ratio" for s in validation.signals)

    # replacement char ratio > 0.01
    replacement = [
        make_item(item_id="p1", item_type="paragraph", text="\ufffd" * 20 + "good text here", order=0, page=1, bbox=BODY_BBOX)
    ]
    validation = _validate(l2_config, replacement, page_count=1)
    assert validation.status == "degraded"
    assert any(not s.ok and s.name == "replacement_char_ratio" for s in validation.signals)

    # suspicious duplicate ratio > 0.15
    duplicates = [
        make_item(item_id=f"p{i}", item_type="paragraph", text="repeated page header.", order=i, page=1, bbox=BODY_BBOX)
        for i in range(10)
    ]
    validation = _validate(l2_config, duplicates, page_count=1)
    assert validation.status == "degraded"
    assert any(not s.ok and s.name == "suspicious_duplicate_ratio" for s in validation.signals)

    # zero headings on a >4 page paper
    no_headings = [
        make_item(item_id=f"p{i}", item_type="paragraph", text=f"body page {i}", order=i, page=(i % 5) + 1, bbox=BODY_BBOX)
        for i in range(10)
    ]
    validation = _validate(l2_config, no_headings, page_count=5)
    assert validation.status == "degraded"
    assert any(not s.ok and s.name == "zero_headings" for s in validation.signals)

    # table/figure references without structured blocks
    missing_struct = [
        make_item(item_id="p1", item_type="paragraph", text="See Table 1 for results.", order=0, page=1, bbox=BODY_BBOX)
    ]
    validation = _validate(l2_config, missing_struct, page_count=1)
    assert validation.status == "degraded"
    assert any(not s.ok and s.name == "structure_missing" for s in validation.signals)

    # reading-order backward jump (hand-built blocks)
    result = _parser_result(missing_struct, page_count=4)
    out = _normalized(l2_config, missing_struct, page_count=4)
    template = out.blocks[0]
    blocks: list[CanonicalBlock] = []
    for index, page in enumerate([4, 2, 3, 1], start=1):
        block = CanonicalBlock(
            block_id=f"blk_000{index:02d}",
            paper_id="p",
            block_type="paragraph",
            section_id=None,
            order=index,
            text=f"Body block {index}.",
            pages=[page],
        )
        from transit_scholar.layer2.schema import CanonicalProvenance

        block.provenance.append(
            CanonicalProvenance(page=page, bbox=BODY_BBOX, char_start=0, char_end=len(block.text))
        )
        blocks.append(block)
    out.blocks = blocks
    out.document.page_count = 4
    validation = ParseValidator(l2_config).validate(result, out.document, out.sections, out.blocks)
    assert validation.status == "degraded"
    assert any(not s.ok and s.name == "reading_order_jumps" for s in validation.signals)


def test_validation_provenance_missing_is_degraded_not_near_empty(l2_config):
    """Parser without page-level provenance (page<=0 items): readable body is
    NOT misjudged as near-empty, no illegal-page hard failure, no blanket
    empty-page report -- a clear ``provenance_missing`` degraded signal."""
    items = [
        make_item(item_id=f"p{i}", item_type="paragraph", text=f"Body text {i}.", order=i, page=0)
        for i in range(5)
    ]
    validation = _validate(l2_config, items, page_count=3)
    assert validation.status == "degraded"
    names = {signal.name for signal in validation.signals}
    assert "provenance_missing" in names
    missing = next(s for s in validation.signals if s.name == "provenance_missing")
    assert missing.ok is False

    near_empty = next(s for s in validation.signals if s.name == "near_empty_body")
    assert near_empty.ok is True
    assert "unknown" in near_empty.message

    ratio_signal = next(
        s for s in validation.signals if s.name == "meaningful_text_page_ratio"
    )
    assert ratio_signal.ok is True
    assert "unknown" in ratio_signal.message

    # no blanket empty-page report without page provenance
    assert not any(s.name == "empty_body_pages" and not s.ok for s in validation.signals)


def test_validation_provenance_missing_never_illegal_page(l2_config):
    """Canonical blocks from page=0 items contain no provenance entries, so
    ``provenance_legal`` stays true (missing provenance is a degraded signal,
    not an illegal page number)."""
    items = [
        make_item(item_id="p1", item_type="paragraph", text="Some body text.", order=0, page=0)
    ]
    result = _parser_result(items, page_count=1)
    out = _normalized(l2_config, items, page_count=1)
    assert all(b.provenance == [] for b in out.blocks)
    validation = ParseValidator(l2_config).validate(
        result, out.document, out.sections, out.blocks
    )
    legal = next(s for s in validation.signals if s.name == "provenance_legal")
    assert legal.ok is True
    assert validation.status == "degraded"


def test_validation_out_of_range_provenance_still_hard_fails(l2_config):
    """Provided (real) provenance that is out of page range remains a hard
    failure -- the missing-provenance leniency never weakens real checks."""
    items = [
        make_item(item_id="p1", item_type="paragraph", text="Some body text.", order=0, page=99, bbox=BODY_BBOX)
    ]
    result = _parser_result(items, page_count=1)
    out = _normalized(l2_config, items, page_count=1)
    validation = ParseValidator(l2_config).validate(
        result, out.document, out.sections, out.blocks
    )
    assert validation.status == "failed"
    assert any(not s.ok and s.name == "provenance_legal" for s in validation.signals)


def test_validation_passed_clean_document(project_tmp_path, monkeypatch, l2_config):
    """A clean multi-page document passes with no degraded signals."""
    items = [
        make_item(
            item_id=f"p{i}", item_type="paragraph",
            text=f"Meaningful body text on page {i}.",
            order=i, page=i + 1, bbox=BODY_BBOX,
        )
        for i in range(3)
    ]
    validation = _validate(l2_config, items, page_count=3)
    assert validation.status == "passed"


def _run_with_parsers(project_tmp_path, monkeypatch, l2_config, adapters):
    from transit_scholar.layer2.pipeline import parse_paper
    from tests.l2s1_fixtures import make_ready_paper, patch_parsers

    paper_id, _file_id, _pdf = make_ready_paper(project_tmp_path)
    patch_parsers(monkeypatch, adapters)
    return parse_paper(paper_id, config=l2_config)


def test_validation_fallback_primary_degraded_fallback_passed(
    project_tmp_path, monkeypatch, l2_config
):
    """AC-L2S1-VALIDATION-04: primary degraded -> whole-document MinerU fallback
    accepted; manifest records the fallback parser."""
    degraded_items = [
        make_item(item_id=f"p{i}", item_type="paragraph", text=f"text {i}", order=i, page=1, bbox=BODY_BBOX)
        for i in range(3)
    ]
    passed_items = [
        make_item(item_id="p1", item_type="paragraph", text="Fallback body text.", order=0, page=1, bbox=BODY_BBOX)
    ]

    class _Primary(FakeParserAdapter):
        name = "docling"

    class _Fallback(FakeParserAdapter):
        name = "mineru"

    adapters = [
        _Primary(items=degraded_items, page_count=10),
        _Fallback(items=passed_items, page_count=1),
    ]
    result = _run_with_parsers(project_tmp_path, monkeypatch, l2_config, adapters)
    assert result.status == "passed"
    assert result.parser_used == "mineru"

    artifacts = read_artifacts(l2_config, result.paper_id, result.parse_run_id)
    # whole-document replacement: only fallback text present, no primary-only text
    blob = "\n".join(b.text for b in artifacts["blocks"])
    assert "Fallback body text." in blob
    assert "text 0" not in blob
    assert artifacts["manifest"]["parser_name"] == "mineru"


def test_validation_fallback_both_failing_needs_review(
    project_tmp_path, monkeypatch, l2_config
):
    """AC-L2S1-VALIDATION-04: both parsers failing -> needs_review."""
    degraded_items = [
        make_item(item_id=f"p{i}", item_type="paragraph", text=f"text {i}", order=i, page=1, bbox=BODY_BBOX)
        for i in range(3)
    ]

    class _Primary(FakeParserAdapter):
        name = "docling"

    class _Fallback(FakeParserAdapter):
        name = "mineru"

    adapters = [
        _Primary(items=degraded_items, page_count=10),
        _Fallback(items=[], page_count=5),
    ]
    result = _run_with_parsers(project_tmp_path, monkeypatch, l2_config, adapters)
    assert result.status == "needs_review"
    assert result.error_code
    # current.json is NOT promoted for needs_review
    from transit_scholar.layer2.paths import load_current

    assert load_current(l2_config.parsed_paper_dir(result.paper_id)) is None


def test_validation_fallback_no_splicing(
    project_tmp_path, monkeypatch, l2_config
):
    """AC-L2S1-VALIDATION-04: mixed partially-good/partially-bad results are
    never spliced; the fallback is a full-document replacement."""
    primary_items = [
        make_item(item_id=f"p{i}", item_type="paragraph", text=f"primary text {i}", order=i, page=1, bbox=BODY_BBOX)
        for i in range(5)
    ]
    fallback_items = [
        make_item(item_id="f1", item_type="paragraph", text="Fallback document line one.", order=0, page=1, bbox=BODY_BBOX),
        make_item(item_id="f2", item_type="paragraph", text="Fallback document line two.", order=1, page=1, bbox=BODY_BBOX),
    ]

    class _Primary(FakeParserAdapter):
        name = "docling"

    class _Fallback(FakeParserAdapter):
        name = "mineru"

    adapters = [
        _Primary(items=primary_items, page_count=10),
        _Fallback(items=fallback_items, page_count=1),
    ]
    result = _run_with_parsers(project_tmp_path, monkeypatch, l2_config, adapters)
    assert result.status == "passed"
    assert result.parser_used == "mineru"
    artifacts = read_artifacts(l2_config, result.paper_id, result.parse_run_id)
    texts = [b.text for b in artifacts["blocks"]]
    # only fallback content; no primary content spliced in
    assert all("primary text" not in t for t in texts)
    assert "Fallback document line one." in texts
    assert "Fallback document line two." in texts


def test_validation_degraded_persisted(project_tmp_path, monkeypatch, l2_config):
    """AC-L2S1-VALIDATION-05: a degraded (accepted) parse is persisted with its
    status and warnings."""
    degraded_items = [
        make_item(item_id=f"p{i}", item_type="paragraph", text=f"text {i}", order=i, page=1, bbox=BODY_BBOX)
        for i in range(3)
    ]
    # single adapter, degraded validation -> accepted as degraded
    adapters = [FakeParserAdapter(items=degraded_items, page_count=10)]
    result = _run_with_parsers(project_tmp_path, monkeypatch, l2_config, adapters)
    assert result.status == "degraded"
    assert result.warnings

    artifacts = read_artifacts(l2_config, result.paper_id, result.parse_run_id)
    assert artifacts["document"].parse_status == "degraded"
    assert artifacts["manifest"]["parse_status"] == "degraded"
    assert artifacts["manifest"]["warnings"]

    from transit_scholar.layer2.paths import load_current

    assert load_current(l2_config.parsed_paper_dir(result.paper_id)) == result.parse_run_id
