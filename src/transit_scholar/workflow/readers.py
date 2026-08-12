"""Read-only queries for the workflow package.

Exposes listing interfaces that the Web API needs but that are not yet
public on the existing services. These functions never mutate state.
"""

from __future__ import annotations

from sqlalchemy import select

from transit_scholar.db.engine import SessionLocal
from transit_scholar.db.models import AuditLog, MetadataCandidate


def list_metadata_candidates(
    *,
    paper_id: str | None = None,
    file_id: str | None = None,
) -> list[MetadataCandidate]:
    """Return metadata candidates, optionally filtered by paper or file."""
    with SessionLocal() as session:
        stmt = select(MetadataCandidate)
        if paper_id is not None:
            stmt = stmt.where(MetadataCandidate.paper_id == paper_id)
        if file_id is not None:
            stmt = stmt.where(MetadataCandidate.paper_file_id == file_id)
        stmt = stmt.order_by(MetadataCandidate.field_name, MetadataCandidate.confidence.desc())
        return list(session.execute(stmt).scalars().all())


def list_audit_logs(
    *,
    entity_type: str | None = None,
    entity_id: str | None = None,
) -> list[AuditLog]:
    """Return audit logs, optionally filtered by entity type and/or id."""
    with SessionLocal() as session:
        stmt = select(AuditLog)
        if entity_type is not None:
            stmt = stmt.where(AuditLog.entity_type == entity_type)
        if entity_id is not None:
            stmt = stmt.where(AuditLog.entity_id == entity_id)
        stmt = stmt.order_by(AuditLog.created_at.desc())
        return list(session.execute(stmt).scalars().all())
