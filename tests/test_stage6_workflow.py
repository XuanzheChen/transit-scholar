"""Stage 6 workflow tests (W01-W18).

Covers the import pipeline, read interfaces, second-layer gate, and the
filename arXiv ID candidate supplement. Reuses the isolated Alembic-head
database from conftest.py and the PDF builders from the Stage 3 tests.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import fitz
import pytest
from sqlalchemy import select

from transit_scholar import config as _config
from transit_scholar.db.engine import SessionLocal
from transit_scholar.db.models import (
    CitationRecord,
    IngestionJob,
    MetadataCandidate,
    Paper,
    PaperAuthor,
    PaperFile,
    PaperRelation,
)
from transit_scholar.identity.service import detect_duplicate_candidates
from transit_scholar.ingestion.service import import_paper
from transit_scholar.metadata.service import extract_metadata_candidates
from transit_scholar.workflow import (
    get_paper,
    get_second_layer_input,
    list_papers,
    reconcile_paper,
    run_import_pipeline,
)
from transit_scholar.workflow.result import (
    PIPELINE_AWAITING_USER_REVIEW,
    PIPELINE_COMPLETED,
    PIPELINE_DUPLICATE,
    PIPELINE_FAILED,
    PIPELINE_PARTIAL,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_database():
    """Clear all business tables before each test for isolation."""
    with SessionLocal() as session:
        session.query(CitationRecord).delete()
        session.query(PaperRelation).delete()
        session.query(MetadataCandidate).delete()
        session.query(PaperAuthor).delete()
        session.query(IngestionJob).delete()
        session.query(PaperFile).delete()
        session.query(Paper).delete()
        session.commit()
    yield


def _make_pdf(
    project_tmp_path: Path,
    *,
    title: str | None = "Test Paper Title",
    author: str | None = "Jane Doe, John Smith",
    doi: str | None = "10.0000/test",
    text: str | None = None,
    filename: str | None = None,
) -> Path:
    """Generate a minimal PDF with metadata and optional first-page text."""
    if filename is None:
        filename = f"test_{uuid.uuid4().hex}.pdf"
    path = project_tmp_path / filename
    doc = fitz.open()
    page = doc.new_page()
    if text:
        rect = fitz.Rect(50, 50, 550, 750)
        page.insert_textbox(rect, text, fontsize=11)
    if title or author:
        meta = {}
        if title:
            meta["title"] = title
        if author:
            meta["author"] = author
        if doi:
            meta["keywords"] = f"doi:{doi}"
        doc.set_metadata(meta)
    doc.save(str(path))
    doc.close()
    return path


def _import_pdf(project_tmp_path: Path, **pdf_kwargs) -> PaperFile:
    """Generate + import a PDF and return the PaperFile record."""
    pdf_path = _make_pdf(project_tmp_path, **pdf_kwargs)
    _config.settings.data_root = project_tmp_path
    result = import_paper(pdf_path)
    assert result.status == "accepted", result.error_message
    with SessionLocal() as session:
        pf = session.get(PaperFile, result.file_id)
        assert pf is not None
        return pf


def _make_paper(
    *,
    title: str | None = None,
    normalized_title: str | None = None,
    doi: str | None = None,
    normalized_doi: str | None = None,
    arxiv_id: str | None = None,
    status: str = "active",
) -> Paper:
    paper = Paper(
        title=title,
        normalized_title=normalized_title,
        doi=doi,
        normalized_doi=normalized_doi,
        arxiv_id=arxiv_id,
        status=status,
    )
    with SessionLocal() as session:
        session.add(paper)
        session.commit()
        session.refresh(paper)
        return paper


def _add_author(paper: Paper, name: str, order: int = 1) -> None:
    with SessionLocal() as session:
        p = session.get(Paper, paper.id)
        session.add(PaperAuthor(
            paper_id=p.id,
            author_order=order,
            full_name=name,
            normalized_name=name.lower(),
        ))
        session.commit()


def _paper_with_primary_file(project_tmp_path: Path) -> tuple[str, str]:
    """Create a paper with one real primary file on disk."""
    _config.settings.data_root = project_tmp_path
    with SessionLocal() as session:
        paper = Paper(status="active")
        session.add(paper)
        session.flush()
        pf = PaperFile(
            paper_id=paper.id,
            is_primary=True,
            relative_path=f"library/originals/placeholder/source.pdf",
        )
        session.add(pf)
        session.flush()
        pf.relative_path = f"library/originals/{pf.id}/source.pdf"
        session.commit()
        paper_id = paper.id
        file_id = pf.id
    disk = project_tmp_path / pf.relative_path
    disk.parent.mkdir(parents=True, exist_ok=True)
    disk.write_bytes(b"%PDF-1.4 fake content for test\n")
    return paper_id, file_id


def _complete_second_layer_metadata(paper_id: str) -> None:
    """Populate the minimum metadata now required by the second-layer gate."""
    with SessionLocal() as session:
        paper = session.get(Paper, paper_id)
        paper.title = "Complete Gate Paper"
        paper.normalized_title = "complete gate paper"
        paper.abstract = "A valid abstract for the second-layer ready gate."
        paper.publication_year = 2024
        paper.doi = "10.9999/gate"
        paper.normalized_doi = "10.9999/gate"
        paper.venue = "Journal of Transit Studies"
        paper.arxiv_id = "2401.00001"
        session.add(PaperAuthor(
            paper_id=paper_id,
            author_order=1,
            full_name="Gate Author",
            normalized_name="gate author",
        ))
        session.commit()


# ---------------------------------------------------------------------------
# W01: new PDF end-to-end pipeline -> completed
# ---------------------------------------------------------------------------


def test_w01_new_pdf_pipeline_completed(project_tmp_path):
    _config.settings.data_root = project_tmp_path
    pdf_path = _make_pdf(project_tmp_path, title="A Fresh Paper", author="Alice Lee")
    result = run_import_pipeline(pdf_path)

    assert result.status == PIPELINE_COMPLETED
    assert result.paper_id is not None
    assert result.file_id is not None
    assert result.job_id is not None
    assert result.is_exact_duplicate is False
    assert result.import_status == "accepted"
    assert result.metadata_status == "extracted"
    assert result.duplicate_status == "completed"
    assert result.current_stage == "completed"
    # Missing year/abstract/venue/arXiv are quality flags, not blockers: the
    # gate is ready and the gaps are expressed in metadata_quality_flags.
    assert result.second_layer_ready is True
    assert result.second_layer_blockers == []
    assert result.metadata_quality_flags == [
        "metadata_missing:year",
        "metadata_missing:abstract",
        "metadata_missing:venue",
        "metadata_missing:arxiv_id",
    ]
    assert "metadata_missing:stable_identifier" not in result.metadata_quality_flags
    assert result.metadata_enrichment_status in ("partial", "pending", "blocked")

    with SessionLocal() as session:
        paper = session.get(Paper, result.paper_id)
        assert paper is not None
        assert paper.status == "active"
        job = session.get(IngestionJob, result.job_id)
        assert job is not None
        assert job.current_stage == "completed"


def test_w01b_missing_doi_stops_before_duplicate_detection(project_tmp_path):
    _config.settings.data_root = project_tmp_path
    pdf_path = _make_pdf(project_tmp_path, title="No DOI Paper", author="Alice Lee", doi=None)
    result = run_import_pipeline(pdf_path)

    assert result.status == PIPELINE_PARTIAL
    assert result.current_stage == "doi_required"
    assert result.duplicate_status is None
    assert result.metadata_enrichment_status == "skipped"
    assert result.enrichment_provider_results == []
    assert result.second_layer_ready is False
    assert result.second_layer_blockers == []
    assert result.metadata_quality_flags == ["stable_identifier_missing:doi"]


# ---------------------------------------------------------------------------
# W02: exact duplicate -> duplicate, short-circuits metadata/detect
# ---------------------------------------------------------------------------


def test_w02_exact_duplicate_short_circuits(project_tmp_path):
    _config.settings.data_root = project_tmp_path
    pdf_path = _make_pdf(project_tmp_path, title="Dup Paper", author="Bob")
    first = run_import_pipeline(pdf_path)
    assert first.status == PIPELINE_COMPLETED

    second = run_import_pipeline(pdf_path)
    assert second.status == PIPELINE_DUPLICATE
    assert second.is_exact_duplicate is True
    assert second.paper_id == first.paper_id
    assert second.metadata_status is None
    assert second.duplicate_status is None
    assert second.second_layer_ready is False

    # No second paper or file was created.
    with SessionLocal() as session:
        assert session.query(Paper).count() == 1
        assert session.query(PaperFile).count() == 1


# ---------------------------------------------------------------------------
# W03: non-PDF / empty file -> failed
# ---------------------------------------------------------------------------


def test_w03_non_pdf_fails(project_tmp_path):
    _config.settings.data_root = project_tmp_path
    bad = project_tmp_path / "notes.txt"
    bad.write_text("not a pdf")
    result = run_import_pipeline(bad)
    assert result.status == PIPELINE_FAILED
    assert result.error_code is not None
    assert result.second_layer_ready is False


# ---------------------------------------------------------------------------
# W04: metadata failed/partial -> partial, second layer blocked
# ---------------------------------------------------------------------------


def test_w04_metadata_failed_returns_partial(project_tmp_path, monkeypatch):
    _config.settings.data_root = project_tmp_path

    # Force metadata extraction to fail inside the pipeline by making read_pdf
    # return a partial result with no metadata and no page text.
    from transit_scholar.metadata import pdf_reader as _pdf_reader

    def _fake_read_pdf(path):
        return _pdf_reader.PdfReadResult(
            partial=True,
            partial_messages=["fake read failure"],
        )

    monkeypatch.setattr(
        "transit_scholar.metadata.service.read_pdf", _fake_read_pdf
    )

    pdf_path = _make_pdf(project_tmp_path, title="Partial Via Pipeline", author="Eve")
    result = run_import_pipeline(pdf_path)

    assert result.status == PIPELINE_PARTIAL
    assert result.metadata_status in {"failed", "partial"}
    assert result.second_layer_ready is False
    assert "metadata_extraction_failed" in result.second_layer_blockers

    with SessionLocal() as session:
        job = session.get(IngestionJob, result.job_id)
        assert job is not None
        assert job.current_stage == "metadata_failed"


# ---------------------------------------------------------------------------
# W05: duplicate detection creates relation -> awaiting_user_review
# ---------------------------------------------------------------------------


def test_w05_duplicate_relation_awaiting_review(project_tmp_path):
    _config.settings.data_root = project_tmp_path
    # Seed an existing paper with a known DOI.
    existing = _make_paper(doi="10.5555/same", normalized_doi="10.5555/same")
    # New PDF whose first-pages text carries the same DOI.
    text = "Introduction.\nDOI: 10.5555/same\nMore text here.\n"
    pdf_path = _make_pdf(project_tmp_path, title="Same DOI Paper", author="Frank", text=text, doi=None)
    result = run_import_pipeline(pdf_path)

    assert result.status == PIPELINE_AWAITING_USER_REVIEW
    assert result.relations_created >= 1
    assert result.current_stage == "awaiting_user_review"
    assert result.second_layer_ready is False
    with SessionLocal() as session:
        paper = session.get(Paper, result.paper_id)
        assert paper.status == "duplicate_pending"


# ---------------------------------------------------------------------------
# W06: no duplicate candidate -> completed, second layer ready
# ---------------------------------------------------------------------------


def test_w06_no_duplicate_completed(project_tmp_path):
    _config.settings.data_root = project_tmp_path
    pdf_path = _make_pdf(
        project_tmp_path,
        title="Unique Topic XYZ",
        author="Grace",
        text="Completely unique content with DOI 10.9999/unique\n",
    )
    result = run_import_pipeline(pdf_path)
    assert result.status == PIPELINE_COMPLETED
    assert result.relations_created == 0
    # Only quality flags remain: missing year/abstract/venue/arXiv do not
    # block the second layer anymore.
    assert result.second_layer_ready is True
    assert result.second_layer_blockers == []
    assert result.metadata_quality_flags == [
        "metadata_missing:year",
        "metadata_missing:abstract",
        "metadata_missing:venue",
        "metadata_missing:arxiv_id",
    ]


# ---------------------------------------------------------------------------
# W07: DOI/arXiv/title-author trigger duplicate detection
# ---------------------------------------------------------------------------


def test_w07_doi_triggers_duplicate(project_tmp_path):
    _config.settings.data_root = project_tmp_path
    _make_paper(doi="10.1234/orig", normalized_doi="10.1234/orig")
    text = "Preprint.\nDOI: 10.1234/orig\nAbstract goes here.\n"
    pdf_path = _make_pdf(project_tmp_path, title="T", author="A", text=text, doi=None)
    result = run_import_pipeline(pdf_path)
    assert result.relations_created >= 1
    with SessionLocal() as session:
        rels = session.query(PaperRelation).all()
        assert any(r.relation_type == "exact_duplicate" for r in rels)


def test_w07_title_author_triggers_probable_duplicate(project_tmp_path):
    _config.settings.data_root = project_tmp_path
    existing = _make_paper(
        title="Shared Title", normalized_title="shared title"
    )
    _add_author(existing, "Alice Lee", order=1)
    text = "Shared Title\nAlice Lee\nMore text follows here.\n"
    pdf_path = _make_pdf(project_tmp_path, title="Shared Title", author="Alice Lee", text=text)
    result = run_import_pipeline(pdf_path)
    assert result.relations_created >= 1


# ---------------------------------------------------------------------------
# W08: job.current_stage is interpretable across stages
# ---------------------------------------------------------------------------


def test_w08_current_stage_interpretable(project_tmp_path):
    _config.settings.data_root = project_tmp_path
    pdf_path = _make_pdf(project_tmp_path, title="Stage Tracker", author="Hank")
    result = run_import_pipeline(pdf_path)
    assert result.current_stage == "completed"
    with SessionLocal() as session:
        job = session.get(IngestionJob, result.job_id)
        assert job.current_stage == "completed"


def test_w08_duplicate_stage(project_tmp_path):
    _config.settings.data_root = project_tmp_path
    pdf_path = _make_pdf(project_tmp_path, title="Dup Stage", author="Ivy")
    run_import_pipeline(pdf_path)
    second = run_import_pipeline(pdf_path)
    assert second.current_stage == "exact_duplicate_check"


# ---------------------------------------------------------------------------
# W09: list_papers returns list with filtering/pagination
# ---------------------------------------------------------------------------


def test_w09_list_papers(project_tmp_path):
    _config.settings.data_root = project_tmp_path
    from datetime import datetime, timezone

    a = _make_paper(title="Alpha", status="active")
    b = _make_paper(title="Beta", status="duplicate_pending")
    g = _make_paper(title="Gamma", status="deleted")
    # Soft-deleted papers are identified by deleted_at, not status alone.
    with SessionLocal() as session:
        gp = session.get(Paper, g.id)
        gp.deleted_at = datetime.now(timezone.utc)
        session.commit()

    # Default excludes soft-deleted papers.
    default_rows = list_papers()
    assert len(default_rows) == 2
    assert all(r.status != "deleted" for r in default_rows)

    # include_deleted=True returns all three.
    all_rows = list_papers(include_deleted=True)
    assert len(all_rows) == 3

    active_rows = list_papers(status="active")
    assert len(active_rows) == 1
    assert active_rows[0].paper_id == a.id

    # Pagination.
    page = list_papers(limit=2, offset=0)
    assert len(page) == 2


# ---------------------------------------------------------------------------
# W10: get_paper returns nested detail
# ---------------------------------------------------------------------------


def test_w10_get_paper_detail(project_tmp_path):
    _config.settings.data_root = project_tmp_path
    paper = _make_paper(
        title="Detail Title",
        normalized_title="detail title",
        doi="10.0000/detail",
        normalized_doi="10.0000/detail",
        arxiv_id="2301.01234",
    )
    _add_author(paper, "Jo Author", order=1)

    detail = get_paper(paper.id)
    assert detail is not None
    assert detail.paper_id == paper.id
    assert detail.title == "Detail Title"
    assert detail.normalized_title == "detail title"
    assert detail.doi == "10.0000/detail"
    assert detail.arxiv_id == "2301.01234"
    assert len(detail.authors) == 1
    assert detail.authors[0]["full_name"] == "Jo Author"
    assert detail.authors[0]["author_order"] == 1

    assert get_paper("doesnotexist1234567890abcdefghijk") is None


# ---------------------------------------------------------------------------
# W11: get_second_layer_input ready
# ---------------------------------------------------------------------------


def test_w11_second_layer_ready(project_tmp_path):
    _config.settings.data_root = project_tmp_path
    paper_id, file_id = _paper_with_primary_file(project_tmp_path)
    # The file on disk is not a real PDF, so metadata extraction would fail.
    # Mark the ingestion job as accepted/completed so the gate reads ready.
    with SessionLocal() as session:
        job = IngestionJob(
            uploaded_filename="x.pdf",
            file_id=file_id,
            paper_id=paper_id,
            status="accepted",
            current_stage="completed",
        )
        session.add(job)
        session.commit()
    _complete_second_layer_metadata(paper_id)

    result = get_second_layer_input(paper_id)
    assert result.status == "ready"
    assert result.paper_id == paper_id
    assert result.primary_file_id == file_id
    assert result.source_pdf_path is not None
    assert Path(result.source_pdf_path).is_file()
    assert result.identity_status == "active"
    assert result.blockers == []
    assert result.metadata_quality_flags == []


# ---------------------------------------------------------------------------
# W12: second layer blocked (deleted paper)
# ---------------------------------------------------------------------------


def test_w12_second_layer_blocked_deleted(project_tmp_path):
    _config.settings.data_root = project_tmp_path
    paper_id, file_id = _paper_with_primary_file(project_tmp_path)
    with SessionLocal() as session:
        paper = session.get(Paper, paper_id)
        paper.status = "deleted"
        session.commit()

    result = get_second_layer_input(paper_id)
    assert result.status == "blocked"
    assert result.source_pdf_path is None
    assert any("paper_not_active" in b for b in result.blockers)


# ---------------------------------------------------------------------------
# W13: second layer blocked (duplicate_pending)
# ---------------------------------------------------------------------------


def test_w13_second_layer_blocked_duplicate_pending(project_tmp_path):
    _config.settings.data_root = project_tmp_path
    paper_id, file_id = _paper_with_primary_file(project_tmp_path)
    with SessionLocal() as session:
        paper = session.get(Paper, paper_id)
        paper.status = "duplicate_pending"
        session.commit()

    result = get_second_layer_input(paper_id)
    assert result.status == "blocked"
    assert any("paper_not_active:duplicate_pending" in b for b in result.blockers)


# ---------------------------------------------------------------------------
# W14: second layer blocked (no primary file)
# ---------------------------------------------------------------------------


def test_w14_second_layer_blocked_no_primary_file(project_tmp_path):
    _config.settings.data_root = project_tmp_path
    paper_id, file_id = _paper_with_primary_file(project_tmp_path)
    with SessionLocal() as session:
        pf = session.get(PaperFile, file_id)
        pf.is_primary = False
        session.commit()

    result = get_second_layer_input(paper_id)
    assert result.status == "blocked"
    assert "no_primary_file" in result.blockers


# ---------------------------------------------------------------------------
# W15: second layer blocked (source file missing on disk)
# ---------------------------------------------------------------------------


def test_w15_second_layer_blocked_source_missing(project_tmp_path):
    _config.settings.data_root = project_tmp_path
    paper_id, file_id = _paper_with_primary_file(project_tmp_path)
    with SessionLocal() as session:
        job = IngestionJob(
            uploaded_filename="x.pdf",
            file_id=file_id,
            paper_id=paper_id,
            status="accepted",
            current_stage="completed",
        )
        session.add(job)
        session.commit()
    # Remove the source file.
    (project_tmp_path / "library/originals" / file_id / "source.pdf").unlink()

    result = get_second_layer_input(paper_id)
    assert result.status == "blocked"
    assert "source_file_missing" in result.blockers


# ---------------------------------------------------------------------------
# W15B: second layer blocked (metadata failed/partial)
# ---------------------------------------------------------------------------


def test_w15b_second_layer_blocked_metadata_failed(project_tmp_path):
    _config.settings.data_root = project_tmp_path
    paper_id, file_id = _paper_with_primary_file(project_tmp_path)
    with SessionLocal() as session:
        job = IngestionJob(
            uploaded_filename="x.pdf",
            file_id=file_id,
            paper_id=paper_id,
            status="accepted",
            current_stage="metadata_failed",
        )
        session.add(job)
        session.commit()

    result = get_second_layer_input(paper_id)
    assert result.status == "blocked"
    assert "metadata_extraction_failed" in result.blockers


def test_w15b_second_layer_blocked_metadata_processing_pending(project_tmp_path):
    _config.settings.data_root = project_tmp_path
    paper_id, file_id = _paper_with_primary_file(project_tmp_path)
    with SessionLocal() as session:
        job = IngestionJob(
            uploaded_filename="x.pdf",
            file_id=file_id,
            paper_id=paper_id,
            status="accepted",
            current_stage="metadata_extracting",
        )
        session.add(job)
        session.commit()

    result = get_second_layer_input(paper_id)
    assert result.status == "blocked"
    assert "metadata_processing_pending" in result.blockers


def test_w15c_second_layer_ready_with_completed_and_duplicate_jobs(project_tmp_path):
    """A later exact-duplicate import job must not block the original paper."""
    _config.settings.data_root = project_tmp_path
    paper_id, file_id = _paper_with_primary_file(project_tmp_path)
    with SessionLocal() as session:
        session.add_all([
            IngestionJob(
                uploaded_filename="original.pdf",
                file_id=file_id,
                paper_id=paper_id,
                status="accepted",
                current_stage="completed",
            ),
            IngestionJob(
                uploaded_filename="duplicate.pdf",
                file_id=file_id,
                paper_id=paper_id,
                status="rejected",
                current_stage="exact_duplicate_check",
            ),
        ])
        session.commit()
    _complete_second_layer_metadata(paper_id)

    result = get_second_layer_input(paper_id)
    assert result.status == "ready"
    assert result.blockers == []


# ---------------------------------------------------------------------------
# W16: filename arXiv ID extraction (valid forms)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("filename,expected", [
    ("2301.01234.pdf", "2301.01234"),
    ("2301.01234v2.pdf", "2301.01234v2"),
    ("arXiv_2301.01234.pdf", "2301.01234"),
    ("arxiv-2301.01234v2.pdf", "2301.01234v2"),
    ("ARXIV 2301.01234.pdf", "2301.01234"),
])
def test_w16_filename_arxiv_valid(project_tmp_path, filename, expected):
    _config.settings.data_root = project_tmp_path
    pdf_path = _make_pdf(project_tmp_path, title="T", author="A", filename=filename)
    result = run_import_pipeline(pdf_path)
    assert result.status == PIPELINE_COMPLETED
    with SessionLocal() as session:
        cand = session.query(MetadataCandidate).filter(
            MetadataCandidate.source_type == "filename_parser",
            MetadataCandidate.field_name == "arxiv_id",
        ).all()
        assert len(cand) == 1
        assert cand[0].value_text == expected
        assert cand[0].source_location == "original_filename"
        assert cand[0].confidence == 0.9


# ---------------------------------------------------------------------------
# W17: filename arXiv ID extraction (reject false positives)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("filename", [
    "cs-9901001.pdf",            # old-style with dash
    "paper_2301.01234.pdf",      # prefix prevents full match
    "2301.01234_supplement.pdf", # suffix prevents full match
    "notes.pdf",                 # no arXiv id
    "1234.56789_extra.pdf",      # suffix after id prevents full match
])
def test_w17_filename_arxiv_rejects(project_tmp_path, filename):
    _config.settings.data_root = project_tmp_path
    pdf_path = _make_pdf(project_tmp_path, title="T", author="A", filename=filename)
    result = run_import_pipeline(pdf_path)
    assert result.status == PIPELINE_COMPLETED
    with SessionLocal() as session:
        cand = session.query(MetadataCandidate).filter(
            MetadataCandidate.source_type == "filename_parser",
        ).all()
        assert cand == [], f"unexpected filename candidates for {filename}: {cand}"


# ---------------------------------------------------------------------------
# W18: pipeline does not write citation_records
# ---------------------------------------------------------------------------


def _paper_with_complete_metadata(project_tmp_path: Path) -> tuple[str, str]:
    """Create a paper with a primary file, completed job, and full metadata."""
    _config.settings.data_root = project_tmp_path
    with SessionLocal() as session:
        paper = Paper(
            title="Complete Paper",
            normalized_title="complete paper",
            abstract="A valid abstract for the second-layer ready gate.",
            doi="10.0000/complete",
            normalized_doi="10.0000/complete",
            publication_year=2024,
            venue="Journal of Transit Studies",
            arxiv_id="2401.00001",
            status="active",
        )
        session.add(paper)
        session.flush()
        session.add(PaperAuthor(
            paper_id=paper.id,
            author_order=1,
            full_name="Complete Author",
            normalized_name="complete author",
        ))
        pf = PaperFile(
            paper_id=paper.id,
            is_primary=True,
            relative_path=f"library/originals/placeholder/source.pdf",
        )
        session.add(pf)
        session.flush()
        pf.relative_path = f"library/originals/{pf.id}/source.pdf"
        session.commit()
        paper_id = paper.id
        file_id = pf.id
    disk = project_tmp_path / pf.relative_path
    disk.parent.mkdir(parents=True, exist_ok=True)
    disk.write_bytes(b"%PDF-1.4 fake content for test\n")
    with SessionLocal() as session:
        session.add(IngestionJob(
            uploaded_filename="x.pdf",
            file_id=file_id,
            paper_id=paper_id,
            status="accepted",
            current_stage="completed",
        ))
        session.commit()
    return paper_id, file_id


# ---------------------------------------------------------------------------
# Stage 7 hotfix: metadata missing blockers (W19-W24)
# ---------------------------------------------------------------------------


def test_w19_second_layer_ready_missing_title(project_tmp_path):
    paper_id, file_id = _paper_with_complete_metadata(project_tmp_path)
    with SessionLocal() as session:
        paper = session.get(Paper, paper_id)
        paper.title = None
        paper.normalized_title = None
        session.commit()
    result = get_second_layer_input(paper_id)
    assert result.status == "ready"
    assert result.blockers == []
    assert "metadata_missing:title" in result.metadata_quality_flags


def test_w20_second_layer_ready_missing_author(project_tmp_path):
    paper_id, file_id = _paper_with_complete_metadata(project_tmp_path)
    with SessionLocal() as session:
        paper = session.get(Paper, paper_id)
        for a in list(paper.authors):
            session.delete(a)
        session.commit()
    result = get_second_layer_input(paper_id)
    assert result.status == "ready"
    assert result.blockers == []
    assert "metadata_missing:author" in result.metadata_quality_flags


def test_w21_second_layer_ready_missing_year(project_tmp_path):
    paper_id, file_id = _paper_with_complete_metadata(project_tmp_path)
    with SessionLocal() as session:
        paper = session.get(Paper, paper_id)
        paper.publication_year = None
        session.commit()
    result = get_second_layer_input(paper_id)
    assert result.status == "ready"
    assert result.blockers == []
    assert "metadata_missing:year" in result.metadata_quality_flags


def test_w22_second_layer_ready_missing_doi(project_tmp_path):
    paper_id, file_id = _paper_with_complete_metadata(project_tmp_path)
    with SessionLocal() as session:
        paper = session.get(Paper, paper_id)
        paper.doi = None
        paper.normalized_doi = None
        paper.arxiv_id = None
        session.commit()
    result = get_second_layer_input(paper_id)
    assert result.status == "ready"
    assert result.blockers == []
    assert "stable_identifier_missing:doi" in result.metadata_quality_flags


def test_w23_second_layer_ready_when_metadata_complete(project_tmp_path):
    paper_id, file_id = _paper_with_complete_metadata(project_tmp_path)
    result = get_second_layer_input(paper_id)
    assert result.status == "ready"
    assert result.blockers == []
    assert result.metadata_quality_flags == []


def test_w24_title_missing_but_doi_present_still_flagged(project_tmp_path):
    paper_id, file_id = _paper_with_complete_metadata(project_tmp_path)
    with SessionLocal() as session:
        paper = session.get(Paper, paper_id)
        paper.title = None
        paper.normalized_title = None
        session.commit()
    result = get_second_layer_input(paper_id)
    assert result.status == "ready"
    assert result.blockers == []
    assert "metadata_missing:title" in result.metadata_quality_flags
    assert "stable_identifier_missing:doi" not in result.metadata_quality_flags


def test_w18_pipeline_writes_no_citations(project_tmp_path):
    _config.settings.data_root = project_tmp_path
    pdf_path = _make_pdf(project_tmp_path, title="No Cite", author="Kim")
    result = run_import_pipeline(pdf_path)
    assert result.status == PIPELINE_COMPLETED
    with SessionLocal() as session:
        assert session.query(CitationRecord).count() == 0


# ---------------------------------------------------------------------------
# W25+: reconcile_paper (AC-RECONCILE)
# ---------------------------------------------------------------------------


def _count_business_rows() -> dict[str, int]:
    """Row counts of every business table reconcile must not grow (AC-RECONCILE-05)."""
    with SessionLocal() as session:
        return {
            "paper": session.query(Paper).count(),
            "paper_file": session.query(PaperFile).count(),
            "paper_author": session.query(PaperAuthor).count(),
            "paper_relation": session.query(PaperRelation).count(),
            "ingestion_job": session.query(IngestionJob).count(),
            "metadata_candidate": session.query(MetadataCandidate).count(),
        }


def test_w25_reconcile_after_manual_fixes_reruns_enrichment_dedup_and_gate(
    project_tmp_path, monkeypatch
):
    """AC-RECONCILE-06-1: after manual field fixes, reconcile_paper reruns DOI
    enrichment and duplicate detection and the gate reflects convergence."""
    from datetime import datetime, timezone

    _config.settings.data_root = project_tmp_path
    monkeypatch.setattr(_config.settings, "metadata_enrichment_allow_network", False)

    pdf_path = _make_pdf(
        project_tmp_path,
        title="Reconcile Paper",
        author="Ann",
        doi="10.1234/recon",
        text="DOI: 10.1234/recon\n",
    )
    first = run_import_pipeline(pdf_path)
    assert first.status == PIPELINE_COMPLETED
    paper_id = first.paper_id

    # Manual fix: add the missing year, abstract and venue.
    from transit_scholar.identity.service import update_paper_metadata

    fix = update_paper_metadata(paper_id, {
        "publication_year": 2023,
        "abstract": "A manually provided abstract.",
        "venue": "Transit Research",
    })
    assert fix.status == "updated"

    result = reconcile_paper(paper_id)
    assert result.status == PIPELINE_COMPLETED
    assert result.paper_id == paper_id
    assert result.file_id == first.file_id
    assert result.current_stage == "completed"
    assert result.metadata_status == "completed"
    # Enrichment ran (never "skipped": a DOI is present). With the network
    # disabled the provider records stay pending (error_code=network_disabled)
    # and are surfaced via enrichment_provider_results; the job status reflects
    # the merged paper state, and after the manual year fix the minimum field
    # set (doi/title/authors/year) is satisfied, so the job reports "fetched".
    assert result.metadata_enrichment_status in ("fetched", "partial", "blocked")
    assert result.duplicate_status == "completed"
    assert result.second_layer_ready is True
    assert result.second_layer_blockers == []
    assert result.metadata_quality_flags == ["metadata_missing:arxiv_id"]
    assert result.error_message


def test_w26_reconcile_without_doi_skips_enrichment_and_flags_doi(
    project_tmp_path, monkeypatch
):
    """AC-RECONCILE-06-2: a paper without DOI reconciles without failure;
    enrichment is skipped and the DOI gap is a quality flag."""
    _config.settings.data_root = project_tmp_path
    monkeypatch.setattr(_config.settings, "metadata_enrichment_allow_network", False)

    pdf_path = _make_pdf(
        project_tmp_path, title="No DOI Reconcile", author="Bo", doi=None
    )
    imported = run_import_pipeline(pdf_path)
    assert imported.status == PIPELINE_PARTIAL
    paper_id = imported.paper_id

    result = reconcile_paper(paper_id)
    assert result.status == PIPELINE_COMPLETED
    assert result.current_stage == "completed"
    assert result.metadata_enrichment_status == "skipped"
    assert result.duplicate_status == "completed"
    assert "stable_identifier_missing:doi" in result.metadata_quality_flags
    assert result.second_layer_ready is True
    assert result.second_layer_blockers == []
    assert result.error_message


def test_w27_reconcile_awaiting_review_then_resolved_converges(
    project_tmp_path, monkeypatch
):
    """AC-RECONCILE-06-3: an existing pending high-risk relation leads to
    awaiting_user_review; after resolve_duplicate settles it, reconcile
    converges to completed and the gate releases."""
    _config.settings.data_root = project_tmp_path
    monkeypatch.setattr(_config.settings, "metadata_enrichment_allow_network", False)

    pdf_path = _make_pdf(
        project_tmp_path, title="Dup Recon Paper", author="Cal", doi="10.1234/dup"
    )
    imported = run_import_pipeline(pdf_path)
    assert imported.status == PIPELINE_COMPLETED
    paper_id = imported.paper_id

    # A pending high-risk relation already exists for the paper.
    with SessionLocal() as session:
        other = Paper(status="active")
        session.add(other)
        session.flush()
        src, tgt = sorted([paper_id, other.id])
        rel = PaperRelation(
            source_paper_id=src,
            target_paper_id=tgt,
            relation_type="probable_duplicate",
            confidence=0.9,
            status="pending",
            reasons_json="[]",
        )
        session.add(rel)
        session.commit()
        relation_id = rel.id

    result = reconcile_paper(paper_id)
    assert result.status == PIPELINE_AWAITING_USER_REVIEW
    assert result.current_stage == "awaiting_user_review"
    assert result.second_layer_blockers == ["pending_duplicate_review"]
    assert result.second_layer_ready is False
    assert result.error_message

    from transit_scholar.identity.service import resolve_duplicate

    resolved = resolve_duplicate(relation_id, "same_paper")
    assert resolved.status == "resolved"

    result = reconcile_paper(paper_id)
    assert result.status == PIPELINE_COMPLETED
    assert result.current_stage == "completed"
    assert result.second_layer_ready is True
    assert result.second_layer_blockers == []
    assert "pending_duplicate_review" not in result.second_layer_blockers


def test_w28_reconcile_is_idempotent(project_tmp_path, monkeypatch):
    """AC-RECONCILE-05: a second reconcile creates no new business rows and
    returns identical gate facts."""
    _config.settings.data_root = project_tmp_path
    monkeypatch.setattr(_config.settings, "metadata_enrichment_allow_network", False)

    pdf_path = _make_pdf(
        project_tmp_path, title="Idem Paper", author="Di", doi="10.1234/idem"
    )
    imported = run_import_pipeline(pdf_path)
    paper_id = imported.paper_id

    first = reconcile_paper(paper_id)
    counts_after_first = _count_business_rows()
    second = reconcile_paper(paper_id)
    counts_after_second = _count_business_rows()

    assert counts_after_second == counts_after_first
    assert second.second_layer_ready == first.second_layer_ready
    assert second.metadata_quality_flags == first.metadata_quality_flags
    assert second.second_layer_blockers == first.second_layer_blockers


def test_w29_reconcile_error_surface(project_tmp_path, monkeypatch):
    """AC-RECONCILE-04/06-5: every error path returns a clear status, code,
    blockers and a non-empty error message."""
    from datetime import datetime, timezone

    _config.settings.data_root = project_tmp_path
    monkeypatch.setattr(_config.settings, "metadata_enrichment_allow_network", False)

    # paper not found
    result = reconcile_paper("doesnotexist1234567890abcdefghijk")
    assert result.status == PIPELINE_FAILED
    assert result.error_code == "PAPER_NOT_FOUND"
    assert result.second_layer_blockers == ["paper_not_found"]
    assert result.error_message

    # paper not active
    paper_id, file_id = _paper_with_primary_file(project_tmp_path)
    with SessionLocal() as session:
        paper = session.get(Paper, paper_id)
        paper.status = "deleted"
        session.commit()
    result = reconcile_paper(paper_id)
    assert result.status == PIPELINE_FAILED
    assert result.error_code == "PAPER_NOT_ACTIVE"
    assert result.second_layer_blockers == ["paper_not_active:deleted"]
    assert result.error_message

    # no primary file
    paper_no_primary = _make_paper(status="active")
    result = reconcile_paper(paper_no_primary.id)
    assert result.status == PIPELINE_FAILED
    assert result.error_code == "NO_PRIMARY_FILE"
    assert result.second_layer_blockers == ["no_primary_file"]
    assert result.error_message

    # primary soft-deleted
    paper_id, file_id = _paper_with_primary_file(project_tmp_path)
    with SessionLocal() as session:
        pf = session.get(PaperFile, file_id)
        pf.deleted_at = datetime.now(timezone.utc)
        session.commit()
    result = reconcile_paper(paper_id)
    assert result.status == PIPELINE_FAILED
    assert result.error_code == "PRIMARY_FILE_DELETED"
    assert result.second_layer_blockers == ["primary_file_deleted"]
    assert result.error_message

    # source file missing on disk
    paper_id, file_id = _paper_with_primary_file(project_tmp_path)
    with SessionLocal() as session:
        pf = session.get(PaperFile, file_id)
        disk = project_tmp_path / pf.relative_path
    disk.unlink()
    result = reconcile_paper(paper_id)
    assert result.status == PIPELINE_FAILED
    assert result.error_code == "SOURCE_FILE_MISSING"
    assert result.second_layer_blockers == ["source_file_missing"]
    assert result.error_message

    # metadata extraction failed: source exists but is not a readable PDF
    paper_id, file_id = _paper_with_primary_file(project_tmp_path)
    result = reconcile_paper(paper_id)
    assert result.status == PIPELINE_FAILED
    assert result.error_code == "METADATA_EXTRACTION_FAILED"
    assert "metadata_extraction_failed" in result.second_layer_blockers
    assert result.error_message


def test_w30_reconcile_duplicate_detection_failed(project_tmp_path, monkeypatch):
    """AC-RECONCILE-04: duplicate detection failure -> partial with the
    detection error code passed through."""
    from transit_scholar.identity.result import DuplicateDetectionResult
    from transit_scholar.workflow import service as workflow_service

    _config.settings.data_root = project_tmp_path
    monkeypatch.setattr(_config.settings, "metadata_enrichment_allow_network", False)

    pdf_path = _make_pdf(
        project_tmp_path, title="Fail Dup Recon", author="Ed", doi="10.1234/fail"
    )
    imported = run_import_pipeline(pdf_path)
    assert imported.status == PIPELINE_COMPLETED

    def _fail_detect(paper_id, *, create_relations=True):
        return DuplicateDetectionResult(
            paper_id=paper_id,
            status="failed",
            candidates_seen=0,
            relations_created=0,
            relations_existing=0,
            relation_ids=[],
            error_code="DATABASE_WRITE_FAILED",
            error_message="simulated detection failure",
        )

    monkeypatch.setattr(workflow_service, "detect_duplicate_candidates", _fail_detect)

    result = reconcile_paper(imported.paper_id)
    assert result.status == PIPELINE_PARTIAL
    assert result.error_code == "DATABASE_WRITE_FAILED"
    assert result.second_layer_blockers == ["duplicate_detection_failed"]
    assert result.error_message
