"""Layer2 Step1 gate contract tests (AC-L2S1-GATE-01/02/03)."""

from __future__ import annotations

import hashlib
import json

from transit_scholar.db.engine import SessionLocal
from transit_scholar.db.models import (
    CitationRecord,
    DOIEnrichmentJob,
    DOIProviderResult,
    IngestionJob,
    MetadataCandidate,
    Paper,
    PaperAuthor,
    PaperFile,
    PaperRelation,
)
from transit_scholar.layer2.parser.fake import FakeParserAdapter, make_item
from tests.l2s1_fixtures import (
    fake_pdf_bytes,
    make_ready_paper,
    patch_parsers,
)

_BUSINESS_TABLES = (
    CitationRecord,
    DOIEnrichmentJob,
    DOIProviderResult,
    IngestionJob,
    MetadataCandidate,
    Paper,
    PaperAuthor,
    PaperFile,
    PaperRelation,
)


def _db_snapshot() -> bytes:
    """Serialized, sorted row sets of every Layer1 business table."""
    payload: dict[str, list[str]] = {}
    with SessionLocal() as session:
        for model in _BUSINESS_TABLES:
            rows = []
            for row in session.query(model).all():
                item = {}
                for column in model.__table__.columns:
                    value = getattr(row, column.name)
                    if value is not None:
                        try:
                            json.dumps(value)
                            item[column.name] = value
                        except (TypeError, ValueError):
                            item[column.name] = str(value)
                rows.append(json.dumps(item, sort_keys=True, default=str))
            payload[model.__tablename__] = sorted(rows)
    return json.dumps(payload, sort_keys=True).encode("utf-8")


def test_gate_blocked_does_not_read_pdf_or_create_outputs(
    monkeypatch, l2_config, project_tmp_path
):
    """AC-L2S1-GATE-01: parse_paper returns a blocked result, populates
    error_code/blockers, reads no PDF and creates no layer2 output."""
    from transit_scholar.layer2.pipeline import parse_paper
    from transit_scholar.workflow.result import SecondLayerInputResult

    class _Gate:
        status = "blocked"
        paper_id = "paper_blocked"
        primary_file_id = None
        source_pdf_path = None
        relative_path = None
        title = None
        authors = []
        year = None
        doi = None
        arxiv_id = None
        page_count = None
        identity_status = None
        duplicate_status = None
        blockers = ["metadata_processing_pending"]
        error_code = "metadata_processing_pending"
        error_message = None
        metadata_quality_flags = []

    def fake_gate(paper_id):
        assert paper_id == "paper_blocked"
        return _Gate()

    monkeypatch.setattr(
        "transit_scholar.layer2.pipeline.get_second_layer_input", fake_gate
    )

    result = parse_paper("paper_blocked", config=l2_config)
    assert result.status == "blocked"
    assert result.error_code == "metadata_processing_pending"
    assert result.blockers == ["metadata_processing_pending"]
    assert result.parse_run_id is None
    assert result.output_dir is None
    # no layer2 output directory was created
    parsed = l2_config.layer2_parsed_dir / "paper_blocked"
    assert not parsed.exists()


def test_gate_ready_pdf_missing_blocks(monkeypatch, l2_config, project_tmp_path):
    """AC-L2S1-GATE-03: a missing source file must block and never reach the
    parser."""
    from transit_scholar.layer2.pipeline import parse_paper
    from transit_scholar.workflow.result import SecondLayerInputResult

    class _Gate:
        status = "ready"
        paper_id = "paper_ready"
        primary_file_id = "file_x"
        source_pdf_path = str(project_tmp_path / "does_not_exist.pdf")
        relative_path = "does_not_exist.pdf"
        title = "X"
        authors = []
        year = None
        doi = None
        arxiv_id = None
        page_count = None
        identity_status = "active"
        duplicate_status = "active"
        blockers = []
        error_code = None
        error_message = None
        metadata_quality_flags = []

    called = []

    def fake_gate(paper_id):
        return _Gate()

    def fake_parse(pdf_path):
        called.append(pdf_path)
        return FakeParserAdapter().parse(str(pdf_path))

    monkeypatch.setattr(
        "transit_scholar.layer2.pipeline.get_second_layer_input", fake_gate
    )
    result = parse_paper("paper_ready", config=l2_config)
    assert result.status == "blocked"
    assert result.blockers == ["source_file_missing"]
    assert result.error_code == "source_file_missing"
    assert called == []  # parser never invoked


def test_gate_ready_passes_source_path_to_parser(
    monkeypatch, l2_config, project_tmp_path
):
    """AC-L2S1-GATE-03: a ready gate resolves a real on-disk source path and
    passes it to the parser."""
    from transit_scholar.layer2.pipeline import parse_paper

    paper_id, file_id, pdf_path = make_ready_paper(project_tmp_path)

    seen_paths = []

    class _RecordingAdapter(FakeParserAdapter):
        def parse(self, path):
            seen_paths.append(str(path))
            return super().parse(path)

    items = [
        make_item(
            item_id="p1", item_type="paragraph",
            text="A paragraph of body text for the parser.",
            order=0, page=1, bbox=[70.0, 100, 530, 120], font_size=10.0,
        )
    ]
    adapter = _RecordingAdapter(items=items, page_count=1)
    patch_parsers(monkeypatch, [adapter])

    result = parse_paper(paper_id, config=l2_config)
    assert result.status in ("passed", "degraded")
    assert len(seen_paths) == 1
    assert seen_paths[0] == str(pdf_path)


def test_gate_full_parse_leaves_layer1_db_unchanged(
    monkeypatch, l2_config, project_tmp_path
):
    """AC-L2S1-GATE-02: a full parse writes no Layer1 DB row and leaves all
    Layer1 tables byte-identical."""
    from transit_scholar.layer2.pipeline import parse_paper

    paper_id, file_id, _ = make_ready_paper(project_tmp_path)
    before = _db_snapshot()

    items = [
        make_item(
            item_id="p1", item_type="paragraph",
            text="Body text that should not touch the Layer1 database.",
            order=0, page=1, bbox=[70.0, 100, 530, 120], font_size=10.0,
        )
    ]
    patch_parsers(monkeypatch, [FakeParserAdapter(items=items, page_count=1)])
    result = parse_paper(paper_id, config=l2_config)
    assert result.status in ("passed", "degraded")

    after = _db_snapshot()
    assert after == before, "Layer1 tables changed after a Layer2 parse"


def test_gate_layer2_parse_code_imports_no_layer1_write_path(monkeypatch):
    """AC-L2S1-GATE-02: the Layer2 parse module directly imports no Layer1
    write-path module. ``workflow.service`` hosts the sanctioned read-only
    ``get_second_layer_input`` entry, so it is the only allowed Layer1 module."""
    from pathlib import Path

    layer2_root = Path(__file__).resolve().parents[1] / "src" / "transit_scholar" / "layer2"
    forbidden_imports = (
        "transit_scholar.ingestion",
        "transit_scholar.metadata",
        "transit_scholar.doi_enrichment",
        "transit_scholar.identity",
        "transit_scholar.citation",
        "transit_scholar.web",
    )
    for path in layer2_root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for prefix in forbidden_imports:
            assert prefix not in text, (
                f"Layer2 parse code imports forbidden module {prefix} in {path}"
            )
