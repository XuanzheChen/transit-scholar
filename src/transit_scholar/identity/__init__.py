"""TransitScholar identity service: paper dedup + manual processing."""

from transit_scholar.identity.result import (
    DuplicateCandidateView,
    DuplicateDetectionResult,
    DuplicateResolutionResult,
    PaperActionResult,
)
from transit_scholar.identity.service import (
    archive_paper,
    detect_duplicate_candidates,
    list_duplicate_candidates,
    resolve_duplicate,
    restore_paper,
    set_primary_file,
    soft_delete_paper,
    update_paper_metadata,
)

__all__ = [
    "detect_duplicate_candidates",
    "list_duplicate_candidates",
    "resolve_duplicate",
    "update_paper_metadata",
    "set_primary_file",
    "archive_paper",
    "soft_delete_paper",
    "restore_paper",
    "DuplicateCandidateView",
    "DuplicateDetectionResult",
    "DuplicateResolutionResult",
    "PaperActionResult",
]
