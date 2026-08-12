"""Ingestion consistency tests (AC-INGEST-001..005).

Proves that filesystem failures after the database commit use precise
file-operation codes (never DATABASE_WRITE_FAILED), that committed records are
reconciled to the retained temporary copy, that status/current_stage stay
within the frozen disjoint vocabularies (including rejection of the legacy
``accepted``/``hashing`` stage values), that maintenance discovers the
reconciled job and residue, and that the external source PDF is never modified
or deleted. All filesystem failures are mocked; no real user data is used.
"""

from __future__ import annotations

import hashlib
import shutil
import uuid
from pathlib import Path

import pytest

from transit_scholar import config as _config
from transit_scholar.db.engine import SessionLocal as _RealSessionLocal
from transit_scholar.db.models import IngestionJob, Paper, PaperFile
from transit_scholar.ingestion import import_paper
from transit_scholar.ingestion import service as _service
from transit_scholar.ingestion.errors import (
    DATABASE_WRITE_FAILED,
    FINAL_MOVE_FAILED,
    ORIGINALS_DIR_CREATE_FAILED,
)
from transit_scholar.maintenance import list_maintenance_items
from transit_scholar.maintenance.service import (
    FAILED_INGESTION_JOB,
    MISSING_ORIGINAL_FILE,
    TEMPORARY_RESIDUE,
)

# Frozen disjoint vocabularies from acceptance.json frozen_contracts.
STATUS_VOCABULARY = {"created", "hashing", "rejected", "failed", "accepted"}
STAGE_VOCABULARY = {
    "temp_copy",
    "sha256",
    "exact_duplicate_check",
    "database_write",
    "final_move",
    "metadata_extracting",
    "metadata_failed",
    "doi_required",
    "duplicate_checking",
    "awaiting_user_review",
    "completed",
}
LEGACY_STAGE_VALUES = {"accepted", "hashing"}


@pytest.fixture(autouse=True)
def _reset_database():
    """Clear all business tables before each test for isolation."""
    with _RealSessionLocal() as session:
        session.query(IngestionJob).delete()
        session.query(PaperFile).delete()
        session.query(Paper).delete()
        session.commit()
    yield


@pytest.fixture
def minimal_pdf(project_tmp_path: Path) -> Path:
    """Create a minimal valid PDF file and return its path."""
    path = project_tmp_path / f"test_{uuid.uuid4().hex}.pdf"
    path.write_bytes(
        b"%PDF-1.4\n"
        b"1 0 obj<</Type/Catalog>>endobj\n"
        b"%%EOF\n"
    )
    return path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _snapshot_source(path: Path) -> dict:
    """Snapshot the external source file's path, bytes and SHA256."""
    return {
        "path": str(path.resolve()),
        "bytes": path.read_bytes(),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def _assert_source_unchanged(path: Path, before: dict) -> None:
    """Assert the external source file was neither moved nor modified."""
    assert str(path.resolve()) == before["path"]
    assert path.is_file()
    assert path.read_bytes() == before["bytes"]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before["sha256"]


def _assert_committed_move_failure(result, project_tmp_path: Path, error_code: str) -> None:
    """Shared assertions for a committed-record originals failure.

    AC-INGEST-001: precise file-operation code, never DATABASE_WRITE_FAILED.
    AC-INGEST-002: result keeps paper_id, file_id, SHA256, original filename,
    the real temporary relative path, status=failed and the precise error.
    AC-INGEST-003: PaperFile.relative_path resolves to the retained temporary
    copy; no nonexistent originals path is stored.
    """
    assert result.status == "failed"
    assert result.error_code == error_code
    assert result.error_code != DATABASE_WRITE_FAILED
    assert result.paper_id is not None
    assert result.file_id is not None
    assert result.sha256 is not None
    assert result.original_filename
    expected_relative = f"library/temporary/{result.job_id}/source.pdf"
    assert result.stored_relative_path == expected_relative

    # The temporary copy is retained and resolvable.
    temp_file = project_tmp_path / "library" / "temporary" / result.job_id / "source.pdf"
    assert temp_file.is_file()
    assert (project_tmp_path / result.stored_relative_path).is_file()

    # No file landed under originals.
    originals = project_tmp_path / "library" / "originals"
    if originals.exists():
        assert list(originals.rglob("source.pdf")) == []

    # Persisted facts: job failed at final_move with the precise error.
    with _RealSessionLocal() as session:
        job = session.get(IngestionJob, result.job_id)
        assert job is not None
        assert job.status == "failed"
        assert job.current_stage == "final_move"
        assert job.error_code == error_code
        assert job.error_message
        assert job.completed_at is not None
        assert job.file_id == result.file_id
        assert job.paper_id == result.paper_id

        # PaperFile.relative_path points at the existing temporary copy only.
        paper_file = session.get(PaperFile, result.file_id)
        assert paper_file is not None
        assert paper_file.relative_path == expected_relative
        assert "originals" not in paper_file.relative_path
        assert (project_tmp_path / paper_file.relative_path).is_file()
        assert paper_file.sha256 == result.sha256


# ---------------------------------------------------------------------------
# Success and exact duplicate
# ---------------------------------------------------------------------------


def test_success_uses_frozen_terminal_pair_and_cleans_temp(
    minimal_pdf, project_tmp_path, monkeypatch
):
    """A successful import ends status=accepted/current_stage=completed."""
    monkeypatch.setattr(_config.settings, "data_root", project_tmp_path)
    before = _snapshot_source(minimal_pdf)

    result = import_paper(minimal_pdf)

    assert result.status == "accepted"
    assert result.error_code is None
    assert result.paper_id is not None
    assert result.file_id is not None
    assert result.sha256 is not None
    expected_relative = f"library/originals/{result.file_id}/source.pdf"
    assert result.stored_relative_path == expected_relative
    assert (project_tmp_path / expected_relative).is_file()

    with _RealSessionLocal() as session:
        job = session.get(IngestionJob, result.job_id)
        assert job.status == "accepted"
        assert job.current_stage == "completed"
        assert job.completed_at is not None
        assert job.file_id == result.file_id
        assert job.paper_id == result.paper_id

    # Temporary copy is cleaned up after a successful move.
    temp_dir = project_tmp_path / "library" / "temporary" / result.job_id
    assert not temp_dir.exists()

    _assert_source_unchanged(minimal_pdf, before)


def test_exact_duplicate_uses_frozen_pair_and_keeps_original(
    minimal_pdf, project_tmp_path, monkeypatch
):
    """An exact duplicate ends status=rejected/current_stage=exact_duplicate_check."""
    monkeypatch.setattr(_config.settings, "data_root", project_tmp_path)
    before = _snapshot_source(minimal_pdf)

    first = import_paper(minimal_pdf)
    second = import_paper(minimal_pdf)

    assert first.status == "accepted"
    assert second.status == "rejected"
    assert second.is_exact_duplicate is True
    assert second.paper_id == first.paper_id
    assert second.file_id == first.file_id
    assert second.sha256 == first.sha256

    with _RealSessionLocal() as session:
        job = session.get(IngestionJob, second.job_id)
        assert job.status == "rejected"
        assert job.current_stage == "exact_duplicate_check"
        assert job.completed_at is not None
        assert session.query(Paper).count() == 1
        assert session.query(PaperFile).count() == 1

    # The duplicate attempt's temporary copy is cleaned up.
    temp_dir = project_tmp_path / "library" / "temporary" / second.job_id
    assert not temp_dir.exists()

    _assert_source_unchanged(minimal_pdf, before)


# ---------------------------------------------------------------------------
# Committed-record filesystem failures
# ---------------------------------------------------------------------------


def test_originals_dir_create_failure_uses_precise_code(
    minimal_pdf, project_tmp_path, monkeypatch
):
    """Originals directory creation failure returns ORIGINALS_DIR_CREATE_FAILED."""
    monkeypatch.setattr(_config.settings, "data_root", project_tmp_path)
    before = _snapshot_source(minimal_pdf)

    real_mkdir = Path.mkdir

    def failing_mkdir(self, *args, **kwargs):
        if "originals" in str(self):
            raise OSError("simulated originals directory creation failure")
        return real_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", failing_mkdir)

    result = import_paper(minimal_pdf)

    _assert_committed_move_failure(result, project_tmp_path, ORIGINALS_DIR_CREATE_FAILED)
    _assert_source_unchanged(minimal_pdf, before)


def test_final_move_failure_uses_precise_code_and_reconciles(
    minimal_pdf, project_tmp_path, monkeypatch
):
    """A final move failure returns FINAL_MOVE_FAILED and reconciles the DB."""
    monkeypatch.setattr(_config.settings, "data_root", project_tmp_path)
    before = _snapshot_source(minimal_pdf)

    real_move = shutil.move

    def failing_move(src, dst, *args, **kwargs):
        if "originals" in str(dst):
            raise OSError("simulated final move failure")
        return real_move(src, dst, *args, **kwargs)

    monkeypatch.setattr(shutil, "move", failing_move)

    result = import_paper(minimal_pdf)

    _assert_committed_move_failure(result, project_tmp_path, FINAL_MOVE_FAILED)
    _assert_source_unchanged(minimal_pdf, before)


def test_committed_failure_visible_in_maintenance(
    minimal_pdf, project_tmp_path, monkeypatch
):
    """Maintenance discovers the reconciled job and retained temporary copy.

    AC-INGEST-003: the reconciled PaperFile must not appear as a missing
    original, and the failed job/temporary residue must be listed.
    """
    monkeypatch.setattr(_config.settings, "data_root", project_tmp_path)

    real_move = shutil.move

    def failing_move(src, dst, *args, **kwargs):
        if "originals" in str(dst):
            raise OSError("simulated final move failure")
        return real_move(src, dst, *args, **kwargs)

    monkeypatch.setattr(shutil, "move", failing_move)

    result = import_paper(minimal_pdf)
    assert result.status == "failed"

    items = list_maintenance_items()
    job_items = [
        i for i in items
        if i.related_job_id == result.job_id
        and i.item_type in (FAILED_INGESTION_JOB, TEMPORARY_RESIDUE)
    ]
    assert len(job_items) >= 1, [i.item_type for i in items]

    missing = [
        i for i in items
        if i.item_type == MISSING_ORIGINAL_FILE
        and i.related_file_id == result.file_id
    ]
    assert missing == []


def test_database_write_failure_uses_precise_code_and_keeps_original(
    minimal_pdf, project_tmp_path, monkeypatch
):
    """A pre-commit DB failure returns DATABASE_WRITE_FAILED with no fake ids."""
    monkeypatch.setattr(_config.settings, "data_root", project_tmp_path)
    before = _snapshot_source(minimal_pdf)

    def failing_factory():
        session = _RealSessionLocal()
        original_commit = session.commit

        def failing_commit():
            # Only the Paper/PaperFile transaction dirties a PaperFile.
            if any(isinstance(obj, PaperFile) for obj in session.dirty):
                raise RuntimeError("simulated database write failure")
            return original_commit()

        session.commit = failing_commit
        return session

    monkeypatch.setattr(_service, "SessionLocal", failing_factory)

    result = import_paper(minimal_pdf)

    assert result.status == "failed"
    assert result.error_code == DATABASE_WRITE_FAILED
    # Pre-commit failure: no fabricated ids or hashes.
    assert result.paper_id is None
    assert result.file_id is None
    assert result.sha256 is None
    assert result.stored_relative_path is None

    with _RealSessionLocal() as session:
        job = session.get(IngestionJob, result.job_id)
        assert job.status == "failed"
        assert job.current_stage == "database_write"
        assert job.error_code == DATABASE_WRITE_FAILED
        assert job.error_message
        assert job.completed_at is not None
        assert job.paper_id is None
        assert job.file_id is None
        # Nothing was committed.
        assert session.query(Paper).count() == 0
        assert session.query(PaperFile).count() == 0

    # The temporary copy is cleaned up only where previously safe.
    temp_dir = project_tmp_path / "library" / "temporary" / result.job_id
    assert not temp_dir.exists()

    _assert_source_unchanged(minimal_pdf, before)


# ---------------------------------------------------------------------------
# Frozen vocabularies and legacy rejection
# ---------------------------------------------------------------------------


def test_service_emits_only_frozen_vocabularies_and_no_legacy_stages(
    minimal_pdf, project_tmp_path, monkeypatch
):
    """Every (status, current_stage) pair the service persists is frozen-valid.

    AC-INGEST-004: status=hashing pairs with current_stage=sha256,
    status=accepted pairs with current_stage=completed, and the legacy
    current_stage values accepted/hashing are rejected everywhere.
    """
    monkeypatch.setattr(_config.settings, "data_root", project_tmp_path)

    observed: list[tuple[str | None, str | None]] = []

    def recording_factory():
        session = _RealSessionLocal()
        original_commit = session.commit

        def recording_commit():
            original_commit()
            with _RealSessionLocal() as snap:
                jobs = snap.query(IngestionJob).all()
                for j in jobs:
                    observed.append((j.status, j.current_stage))

        session.commit = recording_commit
        return session

    monkeypatch.setattr(_service, "SessionLocal", recording_factory)

    # Branch 1: success.
    assert import_paper(minimal_pdf).status == "accepted"
    # Branch 2: exact duplicate.
    assert import_paper(minimal_pdf).status == "rejected"

    # Branch 3: originals directory creation failure.
    real_mkdir = Path.mkdir

    def failing_mkdir(self, *args, **kwargs):
        if "originals" in str(self):
            raise OSError("simulated originals directory creation failure")
        return real_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", failing_mkdir)
    other_pdf = project_tmp_path / f"other_{uuid.uuid4().hex}.pdf"
    other_pdf.write_bytes(b"%PDF-1.4\n2 0 obj<</Type/Page>>endobj\n%%EOF\n")
    assert import_paper(other_pdf).status == "failed"

    # Branch 4: final move failure.
    monkeypatch.setattr(Path, "mkdir", real_mkdir)
    real_move = shutil.move

    def failing_move(src, dst, *args, **kwargs):
        if "originals" in str(dst):
            raise OSError("simulated final move failure")
        return real_move(src, dst, *args, **kwargs)

    monkeypatch.setattr(shutil, "move", failing_move)
    third_pdf = project_tmp_path / f"third_{uuid.uuid4().hex}.pdf"
    third_pdf.write_bytes(b"%PDF-1.4\n3 0 obj<</Type/Page>>endobj\n%%EOF\n")
    assert import_paper(third_pdf).status == "failed"

    assert observed, "no service-emitted records were captured"
    for status, stage in observed:
        assert status in STATUS_VOCABULARY, f"status {status!r} outside frozen vocabulary"
        assert stage is None or stage in STAGE_VOCABULARY, (
            f"current_stage {stage!r} outside frozen vocabulary"
        )
        assert stage not in LEGACY_STAGE_VALUES, (
            f"legacy current_stage {stage!r} must never be emitted"
        )
        if status == "hashing":
            assert stage == "sha256", "status=hashing must pair with current_stage=sha256"
        if status == "accepted":
            assert stage == "completed", "status=accepted must pair with current_stage=completed"

    # The required pairings were actually exercised.
    assert ("hashing", "sha256") in observed
    assert ("accepted", "completed") in observed
    assert ("rejected", "exact_duplicate_check") in observed
    assert ("failed", "final_move") in observed


# ---------------------------------------------------------------------------
# External source preservation for every branch
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "branch",
    ["success", "duplicate", "dir_create", "final_move", "database"],
)
def test_external_source_never_modified_or_deleted_for_every_branch(
    branch, minimal_pdf, project_tmp_path, monkeypatch
):
    """AC-INGEST-005: the external source PDF is never touched in any branch."""
    monkeypatch.setattr(_config.settings, "data_root", project_tmp_path)
    before = _snapshot_source(minimal_pdf)

    if branch == "dir_create":
        real_mkdir = Path.mkdir

        def failing_mkdir(self, *args, **kwargs):
            if "originals" in str(self):
                raise OSError("simulated originals directory creation failure")
            return real_mkdir(self, *args, **kwargs)

        monkeypatch.setattr(Path, "mkdir", failing_mkdir)
    elif branch == "final_move":
        real_move = shutil.move

        def failing_move(src, dst, *args, **kwargs):
            if "originals" in str(dst):
                raise OSError("simulated final move failure")
            return real_move(src, dst, *args, **kwargs)

        monkeypatch.setattr(shutil, "move", failing_move)
    elif branch == "database":
        def failing_factory():
            session = _RealSessionLocal()
            original_commit = session.commit

            def failing_commit():
                if any(isinstance(obj, PaperFile) for obj in session.dirty):
                    raise RuntimeError("simulated database write failure")
                return original_commit()

            session.commit = failing_commit
            return session

        monkeypatch.setattr(_service, "SessionLocal", failing_factory)

    first = import_paper(minimal_pdf)
    if branch == "duplicate":
        second = import_paper(minimal_pdf)
        assert second.status == "rejected"
    else:
        assert first.status in ("accepted", "failed")

    _assert_source_unchanged(minimal_pdf, before)
