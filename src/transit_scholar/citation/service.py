"""Citation service: import / list / select / update / delete / render.

Frozen in Phase 1. All mutations write an audit_log row. The renderer reads
only CitationRecord.structured_json and never reads Paper columns.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from transit_scholar.citation.parser import parse_citation
from transit_scholar.citation.renderer import RENDERER_VERSION, render
from transit_scholar.citation.result import (
    CitationActionResult,
    CitationParseResult,
    CitationRecordView,
    CitationRenderResult,
    CitationRenderView,
)
from transit_scholar.db.engine import SessionLocal
from transit_scholar.db.models import AuditLog, CitationRecord, Paper

# --- Error codes (frozen) --------------------------------------------------
PAPER_NOT_FOUND = "PAPER_NOT_FOUND"
CITATION_RECORD_NOT_FOUND = "CITATION_RECORD_NOT_FOUND"
INVALID_SOURCE_FORMAT = "INVALID_SOURCE_FORMAT"
INVALID_CITATION_CONTENT = "INVALID_CITATION_CONTENT"
PARSE_FAILED = "PARSE_FAILED"
INVALID_STYLE = "INVALID_STYLE"
INVALID_STATE = "INVALID_STATE"
DATABASE_WRITE_FAILED = "DATABASE_WRITE_FAILED"

# --- Citation update rejection codes (frozen matrix, AC-CITE-004/005) --------
SOURCE_FORMAT_WITHOUT_RAW_TEXT = "SOURCE_FORMAT_WITHOUT_RAW_TEXT"
STRUCTURED_DATA_MIXED_WITH_RAW_TEXT = "STRUCTURED_DATA_MIXED_WITH_RAW_TEXT"

# --- Paper status values (frozen, copied from identity.service) -------------
STATUS_ACTIVE = "active"
STATUS_DUPLICATE_PENDING = "duplicate_pending"
STATUS_ARCHIVED = "archived"
STATUS_DELETED = "deleted"

ALLOWED_PAPER_STATUSES = frozenset(
    {STATUS_ACTIVE, STATUS_DUPLICATE_PENDING, STATUS_ARCHIVED}
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _write_audit(
    session,
    *,
    entity_type: str,
    entity_id: str,
    action: str,
    actor_type: str,
    old_value: dict[str, Any] | None = None,
    new_value: dict[str, Any] | None = None,
) -> str:
    log = AuditLog(
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        actor_type=actor_type,
        old_value_json=json.dumps(old_value, ensure_ascii=False) if old_value is not None else None,
        new_value_json=json.dumps(new_value, ensure_ascii=False) if new_value is not None else None,
    )
    session.add(log)
    session.flush()
    return log.id


def _structured_snapshot(record: CitationRecord) -> dict[str, Any]:
    """Best-effort dict snapshot of a record's stored structured JSON."""
    try:
        value = json.loads(record.structured_json) if record.structured_json else {}
        return value if isinstance(value, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _to_view(record: CitationRecord) -> CitationRecordView:
    try:
        structured = json.loads(record.structured_json) if record.structured_json else {}
    except Exception:  # noqa: BLE001
        structured = {}
    try:
        warnings = json.loads(record.parse_warnings_json) if record.parse_warnings_json else []
    except Exception:  # noqa: BLE001
        warnings = []
    return CitationRecordView(
        id=record.id,
        paper_id=record.paper_id,
        source_format=record.source_format,
        raw_text=record.raw_text,
        structured_json=structured if isinstance(structured, dict) else {},
        parse_status=record.parse_status,
        parse_warnings=warnings if isinstance(warnings, list) else [],
        is_selected=record.is_selected,
        created_at=record.created_at,
        updated_at=record.updated_at,
        deleted_at=record.deleted_at,
    )


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------


def import_citation_record(
    paper_id: str,
    *,
    source_format: str,
    raw_text: str | None = None,
    structured_data: dict | None = None,
    is_selected: bool = False,
    actor_type: str = "local_user",
) -> CitationActionResult:
    """Parse and store a new CitationRecord for ``paper_id``."""
    result = CitationActionResult(
        citation_record_id=None,
        paper_id=paper_id,
        status="failed",
        audit_log_id=None,
        error_code=None,
        error_message=None,
    )
    try:
        with SessionLocal() as session:
            paper = session.get(Paper, paper_id)
            if paper is None:
                result.error_code = PAPER_NOT_FOUND
                result.error_message = f"Paper not found: {paper_id}"
                return result
            if paper.status == STATUS_DELETED:
                result.error_code = INVALID_STATE
                result.error_message = f"Paper is deleted: {paper_id}"
                return result
            if paper.status not in ALLOWED_PAPER_STATUSES:
                result.error_code = INVALID_STATE
                result.error_message = f"Paper status does not allow citation import: {paper.status}"
                return result

            parsed: CitationParseResult = parse_citation(
                source_format=source_format,
                raw_text=raw_text,
                structured_data=structured_data,
            )
            if parsed.error_code:
                result.error_code = parsed.error_code
                result.error_message = parsed.error_message
                return result

            record = CitationRecord(
                paper_id=paper_id,
                source_format=source_format,
                raw_text=raw_text,
                structured_json=json.dumps(parsed.structured, ensure_ascii=False),
                parse_status=parsed.parse_status,
                parse_warnings_json=json.dumps(parsed.warnings, ensure_ascii=False),
                is_selected=False,  # set below via exclusive selection if requested
            )
            session.add(record)
            session.flush()

            audit_id: str | None = None
            if is_selected:
                # Clear any other selected record for this paper.
                _clear_selected(session, paper_id, exclude_id=record.id)
                record.is_selected = True
            else:
                # If this is the first citation record, auto-select it.
                existing = session.execute(
                    select(CitationRecord).where(
                        CitationRecord.paper_id == paper_id,
                        CitationRecord.id != record.id,
                    )
                ).scalars().all()
                if not existing:
                    record.is_selected = True

            audit_id = _write_audit(
                session,
                entity_type="citation_record",
                entity_id=record.id,
                action="import_citation_record",
                actor_type=actor_type,
                new_value={
                    "paper_id": paper_id,
                    "source_format": source_format,
                    "parse_status": record.parse_status,
                    "is_selected": record.is_selected,
                },
            )
            session.commit()
            session.refresh(record)

        result.citation_record_id = record.id
        result.status = "created"
        result.audit_log_id = audit_id
        return result
    except Exception as exc:  # noqa: BLE001
        result.error_code = DATABASE_WRITE_FAILED
        result.error_message = f"Import failed: {exc}"
        return result


def _clear_selected(session, paper_id: str, exclude_id: str) -> None:
    for rec in session.execute(
        select(CitationRecord).where(
            CitationRecord.paper_id == paper_id,
            CitationRecord.id != exclude_id,
            CitationRecord.is_selected.is_(True),
            CitationRecord.deleted_at.is_(None),
        )
    ).scalars().all():
        rec.is_selected = False


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------


def list_citation_records(
    paper_id: str,
    *,
    include_deleted: bool = False,
) -> list[CitationRecordView]:
    """Return citation records for a paper, optionally including deleted ones."""
    with SessionLocal() as session:
        stmt = select(CitationRecord).where(CitationRecord.paper_id == paper_id)
        if not include_deleted:
            stmt = stmt.where(CitationRecord.deleted_at.is_(None))
        rows = session.execute(stmt.order_by(CitationRecord.created_at)).scalars().all()
        return [_to_view(r) for r in rows]


def get_selected_citation_record(
    paper_id: str,
) -> CitationRecordView | None:
    """Return the currently selected (non-deleted) citation record, if any."""
    with SessionLocal() as session:
        row = session.execute(
            select(CitationRecord).where(
                CitationRecord.paper_id == paper_id,
                CitationRecord.is_selected.is_(True),
                CitationRecord.deleted_at.is_(None),
            )
        ).scalar_one_or_none()
        return _to_view(row) if row is not None else None


# ---------------------------------------------------------------------------
# Select
# ---------------------------------------------------------------------------


def select_citation_record(
    citation_record_id: str,
    *,
    actor_type: str = "local_user",
) -> CitationActionResult:
    """Make ``citation_record_id`` the sole selected record for its paper."""
    result = CitationActionResult(
        citation_record_id=citation_record_id,
        paper_id=None,
        status="failed",
        audit_log_id=None,
        error_code=None,
        error_message=None,
    )
    try:
        with SessionLocal() as session:
            record = session.get(CitationRecord, citation_record_id)
            if record is None:
                result.citation_record_id = None
                result.error_code = CITATION_RECORD_NOT_FOUND
                result.error_message = f"CitationRecord not found: {citation_record_id}"
                return result
            if record.deleted_at is not None:
                result.error_code = INVALID_STATE
                result.error_message = f"CitationRecord is deleted: {citation_record_id}"
                return result

            paper_id = record.paper_id
            result.paper_id = paper_id
            _clear_selected(session, paper_id, exclude_id=record.id)
            record.is_selected = True

            audit_id = _write_audit(
                session,
                entity_type="citation_record",
                entity_id=record.id,
                action="select_citation_record",
                actor_type=actor_type,
                new_value={"paper_id": paper_id, "is_selected": True},
            )
            session.commit()

        result.status = "selected"
        result.audit_log_id = audit_id
        return result
    except Exception as exc:  # noqa: BLE001
        result.error_code = DATABASE_WRITE_FAILED
        result.error_message = f"Select failed: {exc}"
        return result


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------


def update_citation_record(
    citation_record_id: str,
    *,
    source_format: str | None = None,
    raw_text: str | None = None,
    structured_data: dict | None = None,
    actor_type: str = "local_user",
) -> CitationActionResult:
    """Re-parse and update an existing citation record.

    Frozen input matrix (AC-CITE-004/005):

    - raw_text only: reparses with the STORED source_format; stores the new raw_text.
    - source_format + raw_text: reparses with the NEW source_format; stores both.
    - source_format only: rejected with SOURCE_FORMAT_WITHOUT_RAW_TEXT
      (no DB/audit change).
    - structured_data only: audited MANUAL structured override — normalized via
      the manual_structured parser path, persists canonical structured JSON, and
      preserves the original source_format/raw_text; the audit fact marks the
      override and carries old/new structured values.
    - structured_data mixed with raw_text or source_format: rejected with
      STRUCTURED_DATA_MIXED_WITH_RAW_TEXT (no DB/audit change).
    - no inputs: no-op update (audit only, no field change).
    """
    result = CitationActionResult(
        citation_record_id=citation_record_id,
        paper_id=None,
        status="failed",
        audit_log_id=None,
        error_code=None,
        error_message=None,
    )
    try:
        with SessionLocal() as session:
            record = session.get(CitationRecord, citation_record_id)
            if record is None:
                result.citation_record_id = None
                result.error_code = CITATION_RECORD_NOT_FOUND
                result.error_message = f"CitationRecord not found: {citation_record_id}"
                return result
            if record.deleted_at is not None:
                result.error_code = INVALID_STATE
                result.error_message = f"CitationRecord is deleted: {citation_record_id}"
                return result

            paper_id = record.paper_id
            result.paper_id = paper_id

            # --- Frozen update input matrix (AC-CITE-004/005) ----------------
            # 1. structured_data mixed with raw inputs -> reject, no DB/audit.
            if structured_data is not None and (
                raw_text is not None or source_format is not None
            ):
                result.error_code = STRUCTURED_DATA_MIXED_WITH_RAW_TEXT
                result.error_message = (
                    "structured_data cannot be combined with raw_text or source_format"
                )
                return result
            # 2. source_format without raw_text (and no structured_data) -> reject.
            if source_format is not None and raw_text is None:
                result.error_code = SOURCE_FORMAT_WITHOUT_RAW_TEXT
                result.error_message = (
                    "source_format requires raw_text when no structured_data is supplied"
                )
                return result

            # 3. structured_data-only: audited manual structured override. It
            #    goes through the manual_structured normalization path and
            #    preserves the original source_format/raw_text.
            manual_override = structured_data is not None
            if manual_override:
                parsed = parse_citation(
                    source_format="manual_structured",
                    raw_text=None,
                    structured_data=structured_data,
                )
            elif raw_text is not None or source_format is not None:
                # raw_text-only reparses with the STORED format; source_format
                # plus raw_text reparses with the NEW format and stores both.
                new_source = (
                    source_format if source_format is not None else record.source_format
                )
                parsed = parse_citation(
                    source_format=new_source,
                    raw_text=raw_text,
                    structured_data=None,
                )
            else:
                # No inputs: existing no-op update (audit only, no field change).
                parsed = None

            if parsed is None:
                old_value = {
                    "source_format": record.source_format,
                    "parse_status": record.parse_status,
                }
                new_value = {
                    "source_format": record.source_format,
                    "parse_status": record.parse_status,
                    "paper_id": paper_id,
                }
            else:
                if parsed.error_code:
                    result.error_code = parsed.error_code
                    result.error_message = parsed.error_message
                    return result
                old_value = {
                    "source_format": record.source_format,
                    "raw_text": record.raw_text,
                    "parse_status": record.parse_status,
                    "structured_json": _structured_snapshot(record),
                }
                record.structured_json = json.dumps(parsed.structured, ensure_ascii=False)
                record.parse_status = parsed.parse_status
                record.parse_warnings_json = json.dumps(parsed.warnings, ensure_ascii=False)
                if manual_override:
                    # Original source evidence stays untouched; the audit fact
                    # carries old/new structured values plus the override marker.
                    new_value = {
                        "paper_id": paper_id,
                        "source_format": record.source_format,
                        "raw_text": record.raw_text,
                        "parse_status": record.parse_status,
                        "structured_json": parsed.structured,
                        "manual_structured_override": True,
                    }
                else:
                    record.source_format = new_source
                    record.raw_text = raw_text
                    new_value = {
                        "source_format": record.source_format,
                        "parse_status": record.parse_status,
                        "paper_id": paper_id,
                    }

            audit_id = _write_audit(
                session,
                entity_type="citation_record",
                entity_id=record.id,
                action="update_citation_record",
                actor_type=actor_type,
                old_value=old_value,
                new_value=new_value,
            )
            session.commit()
            session.refresh(record)

        result.status = "updated"
        result.audit_log_id = audit_id
        return result
    except Exception as exc:  # noqa: BLE001
        result.error_code = DATABASE_WRITE_FAILED
        result.error_message = f"Update failed: {exc}"
        return result


# ---------------------------------------------------------------------------
# Soft delete
# ---------------------------------------------------------------------------


def soft_delete_citation_record(
    citation_record_id: str,
    *,
    actor_type: str = "local_user",
) -> CitationActionResult:
    """Soft-delete a citation record; clears its is_selected flag."""
    result = CitationActionResult(
        citation_record_id=citation_record_id,
        paper_id=None,
        status="failed",
        audit_log_id=None,
        error_code=None,
        error_message=None,
    )
    try:
        with SessionLocal() as session:
            record = session.get(CitationRecord, citation_record_id)
            if record is None:
                result.citation_record_id = None
                result.error_code = CITATION_RECORD_NOT_FOUND
                result.error_message = f"CitationRecord not found: {citation_record_id}"
                return result
            if record.deleted_at is not None:
                result.error_code = INVALID_STATE
                result.error_message = f"CitationRecord already deleted: {citation_record_id}"
                return result

            paper_id = record.paper_id
            result.paper_id = paper_id
            was_selected = record.is_selected
            record.deleted_at = _now()
            record.is_selected = False

            audit_id = _write_audit(
                session,
                entity_type="citation_record",
                entity_id=record.id,
                action="soft_delete_citation_record",
                actor_type=actor_type,
                old_value={"deleted_at": None, "is_selected": was_selected},
                new_value={
                    "deleted_at": record.deleted_at.isoformat(),
                    "is_selected": False,
                    "paper_id": paper_id,
                },
            )
            session.commit()

        result.status = "deleted"
        result.audit_log_id = audit_id
        return result
    except Exception as exc:  # noqa: BLE001
        result.error_code = DATABASE_WRITE_FAILED
        result.error_message = f"Soft delete failed: {exc}"
        return result


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------


def render_citation(
    citation_record_id: str,
    *,
    style: str,
    locale: str = "en-US",
    persist: bool = True,
    actor_type: str = "local_user",
) -> CitationRenderResult:
    """Render a citation record in the given style.

    The renderer reads ONLY structured_json; it never reads Paper columns.
    """
    result = CitationRenderResult(
        citation_record_id=citation_record_id,
        style=style,
        locale=locale,
        rendered_text=None,
        renderer_version=RENDERER_VERSION,
        status="failed",
        citation_render_id=None,
        warnings=[],
        audit_log_id=None,
        error_code=None,
        error_message=None,
    )
    try:
        with SessionLocal() as session:
            record = session.get(CitationRecord, citation_record_id)
            if record is None:
                result.citation_record_id = None
                result.error_code = CITATION_RECORD_NOT_FOUND
                result.error_message = f"CitationRecord not found: {citation_record_id}"
                return result
            if record.deleted_at is not None:
                result.error_code = INVALID_STATE
                result.error_message = f"CitationRecord is deleted: {citation_record_id}"
                return result
            if record.parse_status == "failed":
                result.error_code = INVALID_STATE
                result.error_message = (
                    f"CitationRecord parse_status=failed; cannot render: {citation_record_id}"
                )
                return result

            if style not in {"gb_t_7714_2025", "apa_7", "mla_9"}:
                result.error_code = INVALID_STYLE
                result.error_message = f"Unsupported style: {style!r}"
                return result

            try:
                structured = json.loads(record.structured_json) if record.structured_json else {}
            except Exception as exc:  # noqa: BLE001
                result.error_code = PARSE_FAILED
                result.error_message = f"structured_json corrupt: {exc}"
                return result
            if not isinstance(structured, dict):
                result.error_code = PARSE_FAILED
                result.error_message = "structured_json is not a JSON object"
                return result

            rendered_text, warnings = render(structured, style=style)
            result.warnings = warnings

            citation_render_id: str | None = None
            if persist:
                # upsert CitationRender
                from transit_scholar.db.models import CitationRender

                row = session.execute(
                    select(CitationRender).where(
                        CitationRender.citation_record_id == citation_record_id,
                        CitationRender.style == style,
                        CitationRender.locale == locale,
                        CitationRender.renderer_version == RENDERER_VERSION,
                    )
                ).scalar_one_or_none()

                if row is None:
                    row = CitationRender(
                        citation_record_id=citation_record_id,
                        style=style,
                        locale=locale,
                        rendered_text=rendered_text,
                        renderer_version=RENDERER_VERSION,
                    )
                    session.add(row)
                else:
                    row.rendered_text = rendered_text
                session.flush()
                citation_render_id = row.id

            audit_id = _write_audit(
                session,
                entity_type="citation_record",
                entity_id=citation_record_id,
                action="render_citation",
                actor_type=actor_type,
                new_value={
                    "style": style,
                    "locale": locale,
                    "renderer_version": RENDERER_VERSION,
                    "persist": persist,
                    "citation_render_id": citation_render_id,
                },
            )
            session.commit()

        result.rendered_text = rendered_text
        result.status = "rendered"
        result.citation_render_id = citation_render_id
        result.audit_log_id = audit_id
        return result
    except Exception as exc:  # noqa: BLE001
        result.error_code = DATABASE_WRITE_FAILED
        result.error_message = f"Render failed: {exc}"
        return result


# ---------------------------------------------------------------------------
# List renders
# ---------------------------------------------------------------------------


def list_citation_renders(
    citation_record_id: str,
) -> list[CitationRenderView]:
    """Return all cached renders for a citation record."""
    from transit_scholar.db.models import CitationRender

    with SessionLocal() as session:
        rows = session.execute(
            select(CitationRender).where(
                CitationRender.citation_record_id == citation_record_id
            ).order_by(CitationRender.created_at)
        ).scalars().all()
        return [
            CitationRenderView(
                id=r.id,
                citation_record_id=r.citation_record_id,
                style=r.style,
                locale=r.locale,
                rendered_text=r.rendered_text,
                renderer_version=r.renderer_version,
                created_at=r.created_at,
                updated_at=r.updated_at,
            )
            for r in rows
        ]
