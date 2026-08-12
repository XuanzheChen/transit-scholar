"""Return structures for the citation service."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class CitationRecordView:
    """A single citation record as returned by the service layer."""

    id: str
    paper_id: str
    source_format: str
    raw_text: Optional[str]
    structured_json: dict
    parse_status: str
    parse_warnings: list[str]
    is_selected: bool
    created_at: datetime
    updated_at: datetime
    deleted_at: Optional[datetime]


@dataclass
class CitationActionResult:
    """Outcome of a citation mutation call (import / select / update / delete)."""

    citation_record_id: Optional[str]
    paper_id: Optional[str]
    status: str                  # created / updated / selected / deleted / failed
    audit_log_id: Optional[str]
    error_code: Optional[str]
    error_message: Optional[str]


@dataclass
class CitationRenderView:
    """A single rendered citation as returned by the service layer."""

    id: str
    citation_record_id: str
    style: str
    locale: str
    rendered_text: str
    renderer_version: str
    created_at: datetime
    updated_at: datetime


@dataclass
class CitationRenderResult:
    """Outcome of a render_citation() call."""

    citation_record_id: Optional[str]
    style: Optional[str]
    locale: str
    rendered_text: Optional[str]
    renderer_version: str
    status: str                  # rendered / failed
    citation_render_id: Optional[str]
    warnings: list[str]
    audit_log_id: Optional[str]
    error_code: Optional[str]
    error_message: Optional[str]


@dataclass
class CitationParseResult:
    """Internal parser output — not directly exposed as a service return."""

    structured: dict
    parse_status: str            # parsed / partial / failed
    warnings: list[str]
    error_code: Optional[str]
    error_message: Optional[str]
