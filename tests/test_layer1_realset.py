"""Layer1 real-set validation script tests (AC-REALSET-01..06).

All runs use temporary directories and fake PDFs, disable the network via
``settings.metadata_enrichment_allow_network=False`` and never touch real
data. The script module is imported in-process (its transit_scholar imports
are lazy, so the already-bound conftest engine/settings stay in control) and
invoked through ``main(argv)`` so exit codes, stderr hints and written
reports are exercised end to end.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path

import fitz
import pytest

from transit_scholar import config as _config
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
from transit_scholar.workflow import run_import_pipeline

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from validate_layer1_realset import (  # noqa: E402
    REPORT_ENTRY_FIELDS,
    main,
    run_realset_validation,
)


@pytest.fixture(autouse=True)
def _reset_database():
    """Clear all business tables before each test for isolation."""
    with SessionLocal() as session:
        session.query(CitationRecord).delete()
        session.query(PaperRelation).delete()
        session.query(MetadataCandidate).delete()
        session.query(DOIProviderResult).delete()
        session.query(DOIEnrichmentJob).delete()
        session.query(PaperAuthor).delete()
        session.query(IngestionJob).delete()
        session.query(PaperFile).delete()
        session.query(Paper).delete()
        session.commit()
    yield


@pytest.fixture(autouse=True)
def _network_off(monkeypatch):
    """Real-set runs in tests must never touch the network."""
    monkeypatch.setattr(_config.settings, "metadata_enrichment_allow_network", False)


def _make_pdf(
    project_tmp_path: Path,
    *,
    title: str = "Realset Paper Title",
    author: str = "Jane Doe",
    doi: str = "10.5555/realset",
    filename: str | None = None,
) -> Path:
    """Generate a minimal real PDF (fitz) with metadata."""
    if filename is None:
        filename = f"realset_{uuid.uuid4().hex}.pdf"
    path = project_tmp_path / filename
    doc = fitz.open()
    page = doc.new_page()
    page.insert_textbox(
        fitz.Rect(50, 50, 550, 750),
        "Introduction.\nDOI: " + (doi or "") + "\nSome body text.\n",
        fontsize=11,
    )
    meta = {"title": title, "author": author}
    if doi:
        meta["keywords"] = f"doi:{doi}"
    doc.set_metadata(meta)
    doc.save(str(path))
    doc.close()
    return path


def _realset_layout(
    project_tmp_path: Path,
) -> tuple[Path, Path, Path]:
    """Create manifest dir + isolated data root; return (manifest_dir,
    data_root, output_dir)."""
    manifest_dir = project_tmp_path / "real_papers"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    data_root = project_tmp_path / "data_root"
    output_dir = data_root / "realset_reports"
    return manifest_dir, data_root, output_dir


def _write_manifest(manifest_dir: Path, entries: list[dict]) -> Path:
    manifest = manifest_dir / "manifest.json"
    manifest.write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")
    return manifest


def _read_report(output_dir: Path) -> dict:
    report_path = output_dir / "report.json"
    assert report_path.is_file(), "report.json was not written"
    return json.loads(report_path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# AC-REALSET-01/06-1/2: missing / invalid / empty manifest paths
# ---------------------------------------------------------------------------


def test_realset_missing_manifest_hints_and_exits_zero(project_tmp_path, capsys):
    """Manifest missing -> clear stderr hint, exit code 0, no report."""
    _, data_root, output_dir = _realset_layout(project_tmp_path)
    missing = project_tmp_path / "nope" / "manifest.json"

    exit_code = main([
        "--manifest", str(missing),
        "--data-root", str(data_root),
        "--output-dir", str(output_dir),
    ])
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "not found" in captured.err.lower()
    assert not (output_dir / "report.json").exists()


def test_realset_invalid_json_manifest_hints_and_exits_zero(
    project_tmp_path, capsys
):
    """Invalid JSON manifest -> clear stderr hint, exit code 0, no report."""
    manifest_dir, data_root, output_dir = _realset_layout(project_tmp_path)
    manifest = manifest_dir / "manifest.json"
    manifest.write_text("{not valid json", encoding="utf-8")

    exit_code = main([
        "--manifest", str(manifest),
        "--data-root", str(data_root),
        "--output-dir", str(output_dir),
    ])
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "not valid json" in captured.err.lower()
    assert not (output_dir / "report.json").exists()


def test_realset_empty_manifest_hints_and_writes_empty_report(
    project_tmp_path, capsys
):
    """Empty manifest array -> hint, empty report, exit code 0."""
    manifest_dir, data_root, output_dir = _realset_layout(project_tmp_path)
    manifest = _write_manifest(manifest_dir, [])

    exit_code = main([
        "--manifest", str(manifest),
        "--data-root", str(data_root),
        "--output-dir", str(output_dir),
    ])
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "no entries" in captured.err.lower()
    report = _read_report(output_dir)
    assert report["entries"] == []
    assert report["summary"]["total"] == 0
    assert (output_dir / "report.txt").is_file()


# ---------------------------------------------------------------------------
# AC-REALSET-02/03/06-3: single fixture entry, schema error, missing file
# ---------------------------------------------------------------------------


def test_realset_single_fixture_report_fields_gold_diff_and_gate(
    project_tmp_path,
):
    """A single fixture entry produces a complete report entry: gold_diff is
    correct, quality flags/blockers follow the AC-GATE semantics."""
    manifest_dir, data_root, output_dir = _realset_layout(project_tmp_path)
    _make_pdf(manifest_dir, filename="paper.pdf")
    # The gold values deliberately differ from the PDF content so the diff is
    # observable; the author set matches to exercise set comparison.
    manifest = _write_manifest(manifest_dir, [{
        "id": "transit-001",
        "file": "paper.pdf",
        "expected_title": "A Different Gold Title",
        "expected_doi": "10.5555/gold",
        "expected_authors": ["Jane Doe"],
        "expected_year": 2020,
        "notes": "fixture",
    }])

    exit_code = main([
        "--manifest", str(manifest),
        "--data-root", str(data_root),
        "--output-dir", str(output_dir),
    ])
    assert exit_code == 0

    report = _read_report(output_dir)
    assert report["summary"]["total"] == 1
    assert report["summary"]["imported"] == 1
    entry = report["entries"][0]
    assert entry["id"] == "transit-001"
    assert entry["file"] == "paper.pdf"
    assert entry["status"] == "imported"
    assert entry["import_status"] == "accepted"
    assert entry["current_stage"] == "completed"
    assert entry["fields_present"] == {
        "title": True,
        "authors": True,
        "year": False,
        "doi": True,
        "arxiv": False,
        "abstract": False,
        "venue": False,
    }
    assert entry["metadata_quality_flags"] == [
        "metadata_missing:year",
        "metadata_missing:abstract",
        "metadata_missing:venue",
        "metadata_missing:arxiv_id",
    ]
    assert entry["second_layer_ready"] is True
    assert entry["second_layer_blockers"] == []
    assert entry["error_code"] is None
    assert entry["error_message"] is None
    assert entry["trace"]

    # gold_diff: title/doi/year differ; authors match as a set.
    assert entry["gold_diff"]["title"] == {
        "expected": "A Different Gold Title",
        "actual": "Realset Paper Title",
    }
    assert entry["gold_diff"]["doi"] == {
        "expected": "10.5555/gold",
        "actual": "10.5555/realset",
    }
    assert entry["gold_diff"]["year"] == {"expected": 2020, "actual": None}
    assert "authors" not in entry["gold_diff"]


def test_realset_missing_file_and_schema_error_entries_continue(
    project_tmp_path,
):
    """A missing file entry is marked missing_file and a schema-error entry is
    marked schema_error; the remaining valid entry is still processed."""
    manifest_dir, data_root, output_dir = _realset_layout(project_tmp_path)
    _make_pdf(manifest_dir, filename="good.pdf")
    manifest = _write_manifest(manifest_dir, [
        {"id": "transit-good", "file": "good.pdf"},
        {"id": "transit-missing", "file": "not_here.pdf"},
        {"id": "transit-bad"},
    ])

    exit_code = main([
        "--manifest", str(manifest),
        "--data-root", str(data_root),
        "--output-dir", str(output_dir),
    ])
    assert exit_code == 0

    report = _read_report(output_dir)
    by_id = {e["id"]: e for e in report["entries"]}
    assert report["summary"] == {
        "total": 3,
        "imported": 1,
        "duplicate": 0,
        "failed": 0,
        "missing_file": 1,
        "schema_error": 1,
    }
    assert by_id["transit-good"]["status"] == "imported"
    assert by_id["transit-good"]["import_status"] == "accepted"
    assert by_id["transit-missing"]["status"] == "missing_file"
    assert by_id["transit-missing"]["import_status"] is None
    assert by_id["transit-missing"]["trace"]
    assert by_id["transit-bad"]["status"] == "schema_error"
    assert by_id["transit-bad"]["error_code"] == "MANIFEST_SCHEMA_ERROR"


def test_realset_report_entries_have_all_frozen_fields(project_tmp_path):
    """report.json is parseable and every entry carries the full AC-REALSET-03
    field set."""
    manifest_dir, data_root, output_dir = _realset_layout(project_tmp_path)
    _make_pdf(manifest_dir, filename="paper.pdf")
    manifest = _write_manifest(manifest_dir, [
        {"id": "transit-001", "file": "paper.pdf"},
    ])
    assert main([
        "--manifest", str(manifest),
        "--data-root", str(data_root),
        "--output-dir", str(output_dir),
    ]) == 0

    report = _read_report(output_dir)
    assert "generated_at_utc" in report
    assert "summary" in report
    for entry in report["entries"]:
        for field in REPORT_ENTRY_FIELDS:
            assert field in entry, f"report entry missing field {field!r}"
        for field in ("title", "authors", "year", "doi", "arxiv", "abstract", "venue"):
            assert field in entry["fields_present"]
        assert isinstance(entry["trace"], str) and entry["trace"]
        assert isinstance(entry["duplicate_relation_summary"], list)
        assert isinstance(entry["gold_diff"], dict)


# ---------------------------------------------------------------------------
# AC-REALSET-04/05/06-5/6: data safety and idempotence
# ---------------------------------------------------------------------------


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_realset_never_modifies_source_pdfs(project_tmp_path):
    """Source PDF hashes are identical before and after a full run."""
    manifest_dir, data_root, output_dir = _realset_layout(project_tmp_path)
    pdf = _make_pdf(manifest_dir, filename="paper.pdf")
    manifest = _write_manifest(manifest_dir, [
        {"id": "transit-001", "file": "paper.pdf"},
    ])

    before = _sha256(pdf)
    assert main([
        "--manifest", str(manifest),
        "--data-root", str(data_root),
        "--output-dir", str(output_dir),
    ]) == 0
    after = _sha256(pdf)
    assert before == after
    assert pdf.is_file()


def test_realset_second_run_is_idempotent(project_tmp_path):
    """A second run over the same data root hits the exact-duplicate path: no
    new Paper/PaperFile/PaperRelation rows, report overwritten."""
    manifest_dir, data_root, output_dir = _realset_layout(project_tmp_path)
    _make_pdf(manifest_dir, filename="paper.pdf")
    manifest = _write_manifest(manifest_dir, [
        {"id": "transit-001", "file": "paper.pdf"},
    ])
    args = [
        "--manifest", str(manifest),
        "--data-root", str(data_root),
        "--output-dir", str(output_dir),
    ]

    assert main(args) == 0
    first = _read_report(output_dir)
    assert first["entries"][0]["status"] == "imported"

    def _counts() -> dict[str, int]:
        with SessionLocal() as session:
            return {
                "paper": session.query(Paper).count(),
                "paper_file": session.query(PaperFile).count(),
                "paper_relation": session.query(PaperRelation).count(),
            }

    counts_after_first = _counts()
    assert main(args) == 0
    second = _read_report(output_dir)
    assert second["entries"][0]["status"] == "duplicate"
    assert second["entries"][0]["import_status"] == "duplicate"
    assert _counts() == counts_after_first
    assert second["generated_at_utc"] != first["generated_at_utc"]


def test_realset_run_reports_duplicate_relations(project_tmp_path):
    """duplicate_relation_summary exposes relation type/status/confidence."""
    manifest_dir, data_root, output_dir = _realset_layout(project_tmp_path)
    _make_pdf(manifest_dir, filename="paper.pdf")
    manifest = _write_manifest(manifest_dir, [
        {"id": "transit-001", "file": "paper.pdf"},
    ])
    assert main([
        "--manifest", str(manifest),
        "--data-root", str(data_root),
        "--output-dir", str(output_dir),
    ]) == 0

    # A second PDF with the same DOI but different content triggers duplicate
    # detection, creating a pending relation involving the manifest's paper.
    second_pdf = _make_pdf(
        project_tmp_path,
        title="Realset Paper Title",
        author="Jane Doe",
        doi="10.5555/realset",
        filename="variant.pdf",
    )
    run_import_pipeline(second_pdf)
    with SessionLocal() as session:
        assert session.query(PaperRelation).filter_by(status="pending").count() >= 1

    report = run_realset_validation(
        manifest_path=manifest,
        data_root=data_root,
        output_dir=output_dir,
    )
    assert report is not None
    entry = report["entries"][0]
    assert entry["duplicate_relation_summary"], (
        "expected a duplicate relation summary"
    )
    relation = entry["duplicate_relation_summary"][0]
    for key in ("relation_id", "relation_type", "status", "confidence"):
        assert key in relation
    assert relation["status"] == "pending"