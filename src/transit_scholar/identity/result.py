"""Return structures for the identity (paper-dedup / manual processing) service."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class DuplicateCandidateView:
    """A single paper-relation as seen from a paper's perspective."""

    relation_id: str
    source_paper_id: str
    target_paper_id: str
    relation_type: str
    confidence: float
    status: str
    reasons: list[dict[str, object]]


@dataclass
class DuplicateDetectionResult:
    """Outcome of a detect_duplicate_candidates() call."""

    paper_id: str | None
    status: str                  # completed / failed
    candidates_seen: int
    relations_created: int
    relations_existing: int
    relation_ids: list[str]
    error_code: str | None
    error_message: str | None


@dataclass
class DuplicateResolutionResult:
    """Outcome of a resolve_duplicate() call."""

    relation_id: str | None
    status: str                  # resolved / failed
    decision: str | None
    audit_log_id: str | None
    error_code: str | None
    error_message: str | None


@dataclass
class PaperActionResult:
    """Outcome of a paper mutation call (metadata / primary / archive / delete / restore)."""

    paper_id: str | None
    status: str                  # updated / archived / deleted / restored / failed
    updated_fields: list[str]
    audit_log_id: str | None
    error_code: str | None
    error_message: str | None
