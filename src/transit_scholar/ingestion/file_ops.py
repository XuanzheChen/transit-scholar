"""File-level operations for ingestion: validation, copy, hash, move.

Pure filesystem helpers live here so the service layer stays thin and
testable. No database code belongs in this module.
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from transit_scholar.config import settings
from transit_scholar.ingestion.errors import (
    EMPTY_FILE,
    FILE_NOT_READABLE,
    FILE_NOT_FOUND,
    FILE_TOO_LARGE,
    FINAL_MOVE_FAILED,
    HASH_FAILED,
    NOT_A_FILE,
    NOT_PDF,
    ORIGINALS_DIR_CREATE_FAILED,
    TEMP_COPY_FAILED,
    IngestionError,
)

# Minimum valid PDF signature: the file must start with this bytes magic.
PDF_MAGIC = b"%PDF-"
# Chunk size for streaming reads (SHA256 and copy) — avoids loading big PDFs.
CHUNK_SIZE = 1 << 16  # 64 KiB


def validate_source_file(file_path: Path) -> int:
    """Validate the source file and return its size in bytes.

    Raises ``IngestionError`` with a stable error code on any failure.
    """
    if not file_path.exists():
        raise IngestionError(FILE_NOT_FOUND, f"File not found: {file_path}")
    if not file_path.is_file():
        raise IngestionError(NOT_A_FILE, f"Not a regular file: {file_path}")

    size = file_path.stat().st_size
    if size == 0:
        raise IngestionError(EMPTY_FILE, f"File is empty: {file_path}")
    if size > settings.max_file_size_bytes:
        raise IngestionError(
            FILE_TOO_LARGE,
            f"File too large: {size} bytes (limit {settings.max_file_size_bytes})",
        )

    # Must be readable and start with the PDF magic header.
    try:
        with file_path.open("rb") as f:
            header = f.read(len(PDF_MAGIC))
    except OSError as exc:
        raise IngestionError(
            FILE_NOT_READABLE, f"Cannot read file: {file_path}: {exc}"
        ) from exc

    if header.startswith(PDF_MAGIC) is False:
        raise IngestionError(NOT_PDF, f"Not a PDF file: {file_path}")

    return size


def copy_to_temporary(source: Path, job_id: str) -> Path:
    """Copy the source file to ``temporary/<job_id>/source.pdf``.

    Returns the path of the temporary copy.
    """
    dest_dir = settings.temporary_dir / job_id
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / "source.pdf"
        shutil.copy2(source, dest)
    except OSError as exc:
        raise IngestionError(
            TEMP_COPY_FAILED, f"Failed to copy to temporary: {exc}"
        ) from exc
    return dest


def compute_sha256(file_path: Path) -> str:
    """Compute SHA256 of a file using streaming reads."""
    sha = hashlib.sha256()
    try:
        with file_path.open("rb") as f:
            while True:
                chunk = f.read(CHUNK_SIZE)
                if not chunk:
                    break
                sha.update(chunk)
    except OSError as exc:
        raise IngestionError(
            HASH_FAILED, f"Failed to hash file {file_path}: {exc}"
        ) from exc
    return sha.hexdigest()


def move_to_originals(temp_file: Path, file_id: str) -> Path:
    """Move a temporary file into ``originals/<file_id>/source.pdf``.

    Returns the final stored path. Caller must ensure the database
    transaction has already committed before calling this.

    Destination-directory creation is reported as ``ORIGINALS_DIR_CREATE_FAILED``
    and a later move failure as ``FINAL_MOVE_FAILED``; neither is a database
    error. Both failure paths leave the temporary copy untouched.
    """
    dest_dir = settings.originals_dir / file_id
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise IngestionError(
            ORIGINALS_DIR_CREATE_FAILED,
            f"Failed to create originals directory {dest_dir}: {exc}",
        ) from exc
    dest = dest_dir / "source.pdf"
    try:
        # shutil.move works across filesystems if needed.
        shutil.move(str(temp_file), str(dest))
    except (OSError, shutil.Error) as exc:
        raise IngestionError(
            FINAL_MOVE_FAILED, f"Failed to move to originals: {exc}"
        ) from exc
    return dest


def cleanup_temporary(job_id: str) -> None:
    """Remove the temporary directory for a job, ignoring errors."""
    temp_dir = settings.temporary_dir / job_id
    if temp_dir.exists():
        shutil.rmtree(temp_dir, ignore_errors=True)
