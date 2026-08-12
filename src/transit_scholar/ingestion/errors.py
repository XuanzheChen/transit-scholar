"""Error codes and exceptions for the ingestion service."""

from __future__ import annotations


# Stable, machine-readable error codes for failed imports.
FILE_NOT_FOUND = "FILE_NOT_FOUND"
NOT_A_FILE = "NOT_A_FILE"
EMPTY_FILE = "EMPTY_FILE"
FILE_TOO_LARGE = "FILE_TOO_LARGE"
FILE_NOT_READABLE = "FILE_NOT_READABLE"
NOT_PDF = "NOT_PDF"
TEMP_COPY_FAILED = "TEMP_COPY_FAILED"
HASH_FAILED = "HASH_FAILED"
DATABASE_WRITE_FAILED = "DATABASE_WRITE_FAILED"
ORIGINALS_DIR_CREATE_FAILED = "ORIGINALS_DIR_CREATE_FAILED"
FINAL_MOVE_FAILED = "FINAL_MOVE_FAILED"


class IngestionError(Exception):
    """Base exception for ingestion failures."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
