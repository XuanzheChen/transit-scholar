"""Return structure for extract_metadata_candidates()."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class MetadataExtractionResult:
    """Outcome of a single extract_metadata_candidates() call."""

    paper_id: str | None
    file_id: str
    status: str                       # extracted / partial / failed
    candidates_created: int
    candidates_seen: int
    selected_candidate_ids: list[str]
    updated_paper_fields: list[str]
    updated_file_fields: list[str]
    error_code: str | None
    error_message: str | None
