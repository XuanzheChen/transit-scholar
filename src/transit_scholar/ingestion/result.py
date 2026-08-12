"""Return structure for import_paper()."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ImportResult:
    """Outcome of a single import_paper() call.

    Stable shape, safe to assert against in tests.
    """

    job_id: str
    status: str                # accepted / rejected / failed
    paper_id: str | None
    file_id: str | None
    is_exact_duplicate: bool
    original_filename: str
    stored_relative_path: str | None
    sha256: str | None
    message: str
    error_code: str | None
    error_message: str | None
