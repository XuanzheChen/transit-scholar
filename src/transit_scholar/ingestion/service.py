"""File import service: import_paper(file_path).

Reuses the Stage 1 infrastructure (Settings, init_db, ORM models, session
factory). Implements the full file-import flow: validation, temporary copy,
SHA256, exact-duplicate detection, database write, and final move.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from transit_scholar.db.engine import SessionLocal
from transit_scholar.db.models import IngestionJob, Paper, PaperFile
from transit_scholar.ingestion import file_ops
from transit_scholar.ingestion.errors import (
    DATABASE_WRITE_FAILED,
    IngestionError,
)
from transit_scholar.ingestion.result import ImportResult


def import_paper(file_path: str | Path) -> ImportResult:
    """Import a local PDF into the TransitScholar library.

    Returns an ``ImportResult`` describing the outcome. Never raises for
    expected failure modes — they are captured in the result's ``status``,
    ``error_code`` and ``error_message`` fields.
    """
    source = Path(file_path)
    original_filename = source.name

    # --- Create the ingestion job up front so every path is traceable. -----
    job = IngestionJob(
        uploaded_filename=original_filename,
        source_path=str(source),
        status="created",
        started_at=datetime.now(timezone.utc),
    )
    with SessionLocal() as session:
        session.add(job)
        session.commit()
        job_id = job.id

    try:
        return _run_import(session_factory=SessionLocal, job=job, source=source)
    except IngestionError as exc:
        return _fail(job_id, exc.code, exc.message)
    except Exception as exc:  # noqa: BLE001 — capture anything unexpected
        return _fail(job_id, DATABASE_WRITE_FAILED, str(exc))


def _run_import(job: IngestionJob, source: Path, session_factory=SessionLocal) -> ImportResult:
    """Execute the import flow for a validated job."""
    job_id = job.id
    original_filename = source.name

    # --- Step 1: validate the source file. --------------------------------
    file_ops.validate_source_file(source)

    # --- Step 2: copy to temporary. ----------------------------------------
    # status/current_stage stay within the frozen disjoint vocabularies:
    # status=hashing pairs only with current_stage=sha256, and the terminal
    # pair for a successful final move is status=accepted/current_stage=completed.
    _update_job(job_id, status="created", current_stage="temp_copy")
    temp_file = file_ops.copy_to_temporary(source, job_id)

    # --- Step 3: compute SHA256 on the temporary copy. ---------------------
    _update_job(job_id, status="hashing", current_stage="sha256")
    sha = file_ops.compute_sha256(temp_file)

    # --- Step 4: exact-duplicate check. ------------------------------------
    _update_job(job_id, status="created", current_stage="exact_duplicate_check")
    with session_factory() as session:
        existing = session.execute(
            select(PaperFile).where(PaperFile.sha256 == sha)
        ).scalar_one_or_none()

        if existing is not None:
            # Exact duplicate: link to the existing paper/file, reject this job.
            paper_id = existing.paper_id
            _update_job(
                job_id,
                status="rejected",
                current_stage="exact_duplicate_check",
                file_id=existing.id,
                paper_id=paper_id,
                completed_at=datetime.now(timezone.utc),
            )
            file_ops.cleanup_temporary(job_id)
            return _make_result(
                job_id=job_id,
                status="rejected",
                paper_id=paper_id,
                file_id=existing.id,
                is_exact_duplicate=True,
                original_filename=original_filename,
                stored_relative_path=existing.relative_path,
                sha256=sha,
                message="Exact duplicate: file already in library",
            )

    # --- Step 5: new file — write to database first. -----------------------
    _update_job(job_id, status="created", current_stage="database_write")
    file_id = None
    try:
        with session_factory() as session:
            paper = Paper(status="active")
            session.add(paper)
            session.flush()  # assign paper.id
            paper_id = paper.id

            paper_file = PaperFile(
                paper_id=paper_id,
                original_filename=original_filename,
                stored_filename="source.pdf",
                sha256=sha,
                file_size_bytes=temp_file.stat().st_size,
                mime_type="application/pdf",
                is_primary=True,
            )
            session.add(paper_file)
            session.flush()  # assign paper_file.id
            file_id = paper_file.id
            # relative_path uses the file_id so it uniquely locates this file.
            paper_file.relative_path = f"library/originals/{file_id}/source.pdf"

            # Link the job inside the same transaction.
            job = session.get(IngestionJob, job_id)
            job.file_id = file_id
            job.paper_id = paper_id
            job.current_stage = "database_write"
            session.commit()
    except Exception as exc:  # noqa: BLE001
        file_ops.cleanup_temporary(job_id)
        raise IngestionError(DATABASE_WRITE_FAILED, f"Database write failed: {exc}") from exc

    # --- Step 6: move the temporary file to originals (after DB commit). ---
    _update_job(job_id, status="created", current_stage="final_move")
    try:
        file_ops.move_to_originals(temp_file, file_id)
    except IngestionError as exc:
        # The Paper/PaperFile/job transaction already committed, but the file
        # is still in temporary. Reconcile the database facts to the disk
        # facts and return a failed result that keeps the committed ids,
        # SHA256 and the real temporary path — never the erasing _fail path.
        _reconcile_committed_move_failure(job_id, file_id, exc.code, exc.message)
        return _make_result(
            job_id=job_id,
            status="failed",
            paper_id=paper_id,
            file_id=file_id,
            is_exact_duplicate=False,
            original_filename=original_filename,
            stored_relative_path=f"library/temporary/{job_id}/source.pdf",
            sha256=sha,
            message="Import failed after database commit",
            error_code=exc.code,
            error_message=exc.message,
        )

    # --- Step 7: finalize the job and clean up. ---------------------------
    _update_job(
        job_id,
        status="accepted",
        current_stage="completed",
        completed_at=datetime.now(timezone.utc),
    )
    file_ops.cleanup_temporary(job_id)

    return _make_result(
        job_id=job_id,
        status="accepted",
        paper_id=paper_id,
        file_id=file_id,
        is_exact_duplicate=False,
        original_filename=original_filename,
        stored_relative_path=f"library/originals/{file_id}/source.pdf",
        sha256=sha,
        message="File imported successfully",
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _update_job(job_id: str, **fields) -> None:
    """Merge extra fields onto an existing job within its own transaction."""
    with SessionLocal() as session:
        job = session.get(IngestionJob, job_id)
        for key, value in fields.items():
            setattr(job, key, value)
        session.commit()


def _reconcile_committed_move_failure(
    job_id: str, file_id: str, error_code: str, error_message: str
) -> None:
    """Atomically align committed records with the retained temporary copy.

    Called only after the Paper/PaperFile/IngestionJob transaction committed
    but the originals directory creation or final move failed. One transaction
    fails the job with the precise file-operation error and repoints
    ``PaperFile.relative_path`` at ``library/temporary/<job_id>/source.pdf``,
    which is exactly where the retained project copy lives, so the database
    never references a nonexistent originals path.
    """
    now = datetime.now(timezone.utc)
    temporary_relative_path = f"library/temporary/{job_id}/source.pdf"
    with SessionLocal() as session:
        paper_file = session.get(PaperFile, file_id)
        if paper_file is not None:
            paper_file.relative_path = temporary_relative_path
        job = session.get(IngestionJob, job_id)
        job.status = "failed"
        job.current_stage = "final_move"
        job.error_code = error_code
        job.error_message = error_message
        job.completed_at = now
        session.commit()


def _fail(job_id: str, error_code: str, error_message: str) -> ImportResult:
    """Record a failed job and return a failed ImportResult."""
    now = datetime.now(timezone.utc)
    with SessionLocal() as session:
        job = session.get(IngestionJob, job_id)
        job.status = "failed"
        job.error_code = error_code
        job.error_message = error_message
        job.completed_at = now
        session.commit()
    return _make_result(
        job_id=job_id,
        status="failed",
        paper_id=None,
        file_id=None,
        is_exact_duplicate=False,
        original_filename="",
        stored_relative_path=None,
        sha256=None,
        message="Import failed",
        error_code=error_code,
        error_message=error_message,
    )


def _make_result(
    job_id: str,
    status: str,
    paper_id: str | None,
    file_id: str | None,
    is_exact_duplicate: bool,
    original_filename: str,
    stored_relative_path: str | None,
    sha256: str | None,
    message: str,
    error_code: str | None = None,
    error_message: str | None = None,
) -> ImportResult:
    return ImportResult(
        job_id=job_id,
        status=status,
        paper_id=paper_id,
        file_id=file_id,
        is_exact_duplicate=is_exact_duplicate,
        original_filename=original_filename,
        stored_relative_path=stored_relative_path,
        sha256=sha256,
        message=message,
        error_code=error_code,
        error_message=error_message,
    )
