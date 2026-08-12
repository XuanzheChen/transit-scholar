"""Stage 2 automated tests for the file import service.

Covers: new file import, exact-duplicate detection, same-name-different-content,
non-PDF rejection, empty-file rejection, and database/fileystem consistency.

No real paper PDFs are used — a minimal valid PDF byte string is generated
by the ``minimal_pdf`` fixture.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

from transit_scholar import config as _config
from transit_scholar.db.engine import SessionLocal
from transit_scholar.db.models import IngestionJob, Paper, PaperFile
from transit_scholar.ingestion import ImportResult, import_paper
from transit_scholar.ingestion.errors import (
    EMPTY_FILE,
    FILE_NOT_FOUND,
    FINAL_MOVE_FAILED,
    NOT_A_FILE,
    NOT_PDF,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_database():
    """Clear all business tables before each test for isolation."""
    with SessionLocal() as session:
        session.query(IngestionJob).delete()
        session.query(PaperFile).delete()
        session.query(Paper).delete()
        session.commit()
    yield


@pytest.fixture
def minimal_pdf(project_tmp_path: Path) -> Path:
    """Create a minimal valid PDF file and return its path.

    The content is just enough to pass the ``%PDF-`` magic check and be a
    non-empty readable file. It is not a renderable PDF.
    """
    path = project_tmp_path / f"test_{uuid.uuid4().hex}.pdf"
    path.write_bytes(
        b"%PDF-1.4\n"
        b"1 0 obj<</Type/Catalog>>endobj\n"
        b"%%EOF\n"
    )
    return path


@pytest.fixture
def non_pdf_file(project_tmp_path: Path) -> Path:
    """Create a plain text file with a .pdf extension."""
    path = project_tmp_path / "not_really.pdf"
    path.write_text("This is just text, not a PDF.")
    return path


@pytest.fixture
def empty_pdf(project_tmp_path: Path) -> Path:
    """Create an empty file with a .pdf extension."""
    path = project_tmp_path / "empty.pdf"
    path.write_bytes(b"")
    return path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _job(session, job_id: str) -> IngestionJob:
    return session.get(IngestionJob, job_id)


def _count(session, model) -> int:
    return session.execute(select(model)).scalars().all().__len__()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_import_new_pdf_creates_records(minimal_pdf, project_tmp_path, monkeypatch):
    """Importing a new PDF creates Paper, PaperFile, and IngestionJob."""
    monkeypatch.setattr(_config.settings, "data_root", project_tmp_path)
    result = import_paper(minimal_pdf)

    assert result.status == "accepted"
    assert result.is_exact_duplicate is False
    assert result.paper_id is not None
    assert result.file_id is not None
    assert result.sha256 is not None
    assert result.error_code is None

    with SessionLocal() as session:
        assert _count(session, Paper) == 1
        assert _count(session, PaperFile) == 1
        jobs = session.execute(select(IngestionJob)).scalars().all()
        assert len(jobs) == 1
        assert jobs[0].status == "accepted"
        assert jobs[0].file_id == result.file_id
        assert jobs[0].paper_id == result.paper_id


def test_imported_file_saved_to_originals(minimal_pdf, project_tmp_path, monkeypatch):
    """The final file is stored at library/originals/<file_id>/source.pdf."""
    monkeypatch.setattr(_config.settings, "data_root", project_tmp_path)
    result = import_paper(minimal_pdf)

    expected = project_tmp_path / "library" / "originals" / result.file_id / "source.pdf"
    assert expected.is_file()
    assert result.stored_relative_path == f"library/originals/{result.file_id}/source.pdf"


def test_relative_path_locates_file(minimal_pdf, project_tmp_path, monkeypatch):
    """relative_path from the DB can locate the real file on disk."""
    monkeypatch.setattr(_config.settings, "data_root", project_tmp_path)
    result = import_paper(minimal_pdf)

    with SessionLocal() as session:
        pf = session.get(PaperFile, result.file_id)
        on_disk = project_tmp_path / pf.relative_path
        assert on_disk.is_file()


def test_stored_filename_is_source_pdf(minimal_pdf, project_tmp_path, monkeypatch):
    """stored_filename is always 'source.pdf' regardless of original name."""
    monkeypatch.setattr(_config.settings, "data_root", project_tmp_path)
    result = import_paper(minimal_pdf)
    with SessionLocal() as session:
        pf = session.get(PaperFile, result.file_id)
        assert pf.stored_filename == "source.pdf"


def test_original_filename_recorded_but_not_used_in_path(
    minimal_pdf, project_tmp_path, monkeypatch
):
    """original_filename is saved for traceability but does not affect storage path."""
    original_name = minimal_pdf.name
    monkeypatch.setattr(_config.settings, "data_root", project_tmp_path)
    result = import_paper(minimal_pdf)
    assert result.original_filename == original_name

    with SessionLocal() as session:
        pf = session.get(PaperFile, result.file_id)
        assert pf.original_filename == original_name
        # The storage path uses the file_id, never the original filename.
        assert original_name not in pf.relative_path


def test_same_pdf_imported_twice_is_exact_duplicate(
    minimal_pdf, project_tmp_path, monkeypatch
):
    """Importing the same PDF twice does not create a second PaperFile."""
    monkeypatch.setattr(_config.settings, "data_root", project_tmp_path)
    first = import_paper(minimal_pdf)
    second = import_paper(minimal_pdf)

    assert first.status == "accepted"
    assert second.status == "rejected"
    assert second.is_exact_duplicate is True
    assert second.paper_id == first.paper_id
    assert second.file_id == first.file_id

    with SessionLocal() as session:
        assert _count(session, Paper) == 1
        assert _count(session, PaperFile) == 1
        assert _count(session, IngestionJob) == 2


def test_different_name_same_content_is_duplicate(
    minimal_pdf, project_tmp_path, monkeypatch
):
    """Same content under a different filename is still an exact duplicate."""
    monkeypatch.setattr(_config.settings, "data_root", project_tmp_path)
    import_paper(minimal_pdf)

    # Copy the same bytes to a differently-named file.
    renamed = project_tmp_path / "renamed.pdf"
    renamed.write_bytes(minimal_pdf.read_bytes())
    second = import_paper(renamed)

    assert second.is_exact_duplicate is True
    assert second.status == "rejected"


def test_same_name_different_content_not_overwritten(
    minimal_pdf, project_tmp_path, monkeypatch
):
    """Same filename but different content gets a distinct file_id and path."""
    monkeypatch.setattr(_config.settings, "data_root", project_tmp_path)
    first = import_paper(minimal_pdf)

    # Overwrite the source file with different bytes, same name.
    minimal_pdf.write_bytes(b"%PDF-1.4\n2 0 obj<</Type/Page>>endobj\n%%EOF\n")
    second = import_paper(minimal_pdf)

    assert second.status == "accepted"
    assert second.is_exact_duplicate is False
    assert second.file_id != first.file_id

    with SessionLocal() as session:
        assert _count(session, PaperFile) == 2
    # Both files exist on disk.
    f1 = project_tmp_path / "library" / "originals" / first.file_id / "source.pdf"
    f2 = project_tmp_path / "library" / "originals" / second.file_id / "source.pdf"
    assert f1.is_file() and f2.is_file()


def test_non_pdf_rejected(non_pdf_file, project_tmp_path, monkeypatch):
    """A file that does not start with %PDF- is rejected."""
    monkeypatch.setattr(_config.settings, "data_root", project_tmp_path)
    result = import_paper(non_pdf_file)

    assert result.status == "failed"
    assert result.error_code == NOT_PDF
    with SessionLocal() as session:
        assert _count(session, Paper) == 0
        assert _count(session, PaperFile) == 0


def test_empty_file_rejected(empty_pdf, project_tmp_path, monkeypatch):
    """An empty file is rejected with EMPTY_FILE."""
    monkeypatch.setattr(_config.settings, "data_root", project_tmp_path)
    result = import_paper(empty_pdf)

    assert result.status == "failed"
    assert result.error_code == EMPTY_FILE


def test_missing_file_rejected(project_tmp_path, monkeypatch):
    """A non-existent path is rejected with FILE_NOT_FOUND."""
    monkeypatch.setattr(_config.settings, "data_root", project_tmp_path)
    result = import_paper(project_tmp_path / "does_not_exist.pdf")

    assert result.status == "failed"
    assert result.error_code == FILE_NOT_FOUND


def test_directory_rejected(project_tmp_path, monkeypatch):
    """Passing a directory path is rejected with NOT_A_FILE."""
    monkeypatch.setattr(_config.settings, "data_root", project_tmp_path)
    result = import_paper(project_tmp_path)

    assert result.status == "failed"
    assert result.error_code == NOT_A_FILE


def test_failure_leaves_no_formal_file(non_pdf_file, project_tmp_path, monkeypatch):
    """A failed import does not create a file in originals."""
    monkeypatch.setattr(_config.settings, "data_root", project_tmp_path)
    import_paper(non_pdf_file)

    originals = project_tmp_path / "library" / "originals"
    if originals.exists():
        # No subdirectories (each file_id gets its own) should exist.
        assert list(originals.iterdir()) == []


def test_failed_job_records_error_details(
    non_pdf_file, project_tmp_path, monkeypatch
):
    """A failed job has status=failed, error_code, and error_message set."""
    monkeypatch.setattr(_config.settings, "data_root", project_tmp_path)
    result = import_paper(non_pdf_file)

    with SessionLocal() as session:
        job = session.get(IngestionJob, result.job_id)
        assert job.status == "failed"
        assert job.error_code == NOT_PDF
        assert job.error_message
        assert job.completed_at is not None


def test_final_move_failure_retains_temporary(
    minimal_pdf, project_tmp_path, monkeypatch
):
    """If the final move fails, the job is failed but the temporary copy is kept."""
    import shutil

    monkeypatch.setattr(_config.settings, "data_root", project_tmp_path)
    # Let validation and DB write succeed, but make the final move fail.
    real_move = shutil.move

    def failing_move(src, dst, *args, **kwargs):
        # Only fail when moving into the originals directory.
        if "originals" in str(dst):
            raise OSError("simulated move failure")
        return real_move(src, dst, *args, **kwargs)

    monkeypatch.setattr(shutil, "move", failing_move)

    result = import_paper(minimal_pdf)

    assert result.status == "failed"
    assert result.error_code == FINAL_MOVE_FAILED
    # The temporary copy should be retained for diagnosis.
    from transit_scholar.ingestion import file_ops as _fo

    temp_dir = project_tmp_path / "library" / "temporary" / result.job_id
    assert temp_dir.is_dir()
    assert (temp_dir / "source.pdf").is_file()
    # No formal file should have been created (the move failed before writing).
    originals = project_tmp_path / "library" / "originals"
    if originals.exists():
        # There must be no source.pdf file anywhere under originals.
        assert list(originals.rglob("source.pdf")) == []
