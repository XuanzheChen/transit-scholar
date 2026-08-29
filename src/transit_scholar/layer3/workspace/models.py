"""Normalized Workspace control-plane snapshots (Layer3 Stage1).

Pydantic value objects reconstructed from the persistent ORM rows. They are
immutable snapshots for upper-layer consumption; mutation goes exclusively
through the ``WorkspaceService``.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

if TYPE_CHECKING:  # pragma: no cover - typing only
    from transit_scholar.db.models import (
        Workspace as WorkspaceRow,
        WorkspacePaperMembership as MembershipRow,
    )

WorkspaceStatus = Literal["active", "archived", "deleting", "deleted"]
SchemaMode = Literal["bound", "none"]

#: Derived per-Paper Workspace Schema availability (AC-014): disabled (no-schema
#: Workspace), missing (no Workspace-owned current run yet) or ready.
PaperSchemaStatus = Literal["disabled", "missing", "ready"]


class SchemaBinding(BaseModel):
    """Immutable bound-Schema identity persisted at Workspace creation."""

    schema_id: str = Field(min_length=1)
    schema_version: str = Field(min_length=1)
    schema_hash: str = Field(min_length=1)


class WorkspaceRecord(BaseModel):
    """Read snapshot of a persistent Workspace (REQ-001)."""

    workspace_id: str
    name: str
    status: WorkspaceStatus
    schema_mode: SchemaMode
    schema_binding: SchemaBinding | None = None
    revision: int = Field(ge=1)
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_row(cls, row: "WorkspaceRow") -> "WorkspaceRecord":
        binding = None
        if row.schema_mode == "bound":
            binding = SchemaBinding(
                schema_id=row.schema_id,
                schema_version=row.schema_version,
                schema_hash=row.schema_hash,
            )
        return cls(
            workspace_id=row.id,
            name=row.name,
            status=row.status,
            schema_mode=row.schema_mode,
            schema_binding=binding,
            revision=row.revision,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


class MembershipRecord(BaseModel):
    """One Workspace-to-Paper membership row (REQ-002)."""

    workspace_id: str
    paper_id: str
    created_at: datetime

    @classmethod
    def from_row(cls, row: "MembershipRow") -> "MembershipRecord":
        return cls(
            workspace_id=row.workspace_id,
            paper_id=row.paper_id,
            created_at=row.created_at,
        )


class CreateWorkspaceResult(BaseModel):
    """Outcome of ``WorkspaceService.create``."""

    workspace: WorkspaceRecord


class AddPaperResult(BaseModel):
    """Outcome of ``WorkspaceService.add_paper``.

    ``already_member`` is True when the membership already existed and the
    call was a no-op (idempotent contract, AC-003): no duplicate row is ever
    created and the Workspace revision is not advanced.
    """

    workspace: WorkspaceRecord
    membership: MembershipRecord
    already_member: bool = False


class RemovePaperResult(BaseModel):
    """Outcome of ``WorkspaceService.remove_paper``."""

    workspace: WorkspaceRecord
    paper_id: str


class ArchiveWorkspaceResult(BaseModel):
    """Outcome of ``WorkspaceService.archive`` (REQ-009 / AC-016).

    ``already_archived`` is True when the Workspace was already archived and
    the call was a no-op (idempotent contract): memberships and Workspace-owned
    files are preserved and the revision is not advanced again.
    """

    workspace: WorkspaceRecord
    already_archived: bool = False


class DeleteWorkspaceResult(BaseModel):
    """Outcome of ``WorkspaceService.delete`` (REQ-009 / AC-017).

    ``already_deleted`` is True when the Workspace was already in the deleted
    tombstone state and the call was a no-op (idempotent contract): no
    repeated destructive cleanup is performed and the revision is unchanged.
    """

    workspace: WorkspaceRecord
    already_deleted: bool = False


class WorkspacePaperView(BaseModel):
    """Normalized read view of one visible Paper (REQ-010, AC-014).

    ``l2s1_ready`` is derived by read-only inspection of the global L2S1
    canonical parse pointer and retrieval index (never built here).
    ``schema_status`` is derived from the Workspace schema mode and the
    Workspace-owned current Schema run: ``disabled`` for none-mode
    Workspaces, ``missing`` when no run exists yet, ``ready`` when a current
    run is present.
    """

    workspace_id: str
    paper_id: str
    title: str | None = None
    paper_status: str = "active"
    l2s1_ready: bool = False
    schema_status: PaperSchemaStatus = "disabled"


__all__ = [
    "SchemaBinding",
    "WorkspaceRecord",
    "MembershipRecord",
    "CreateWorkspaceResult",
    "AddPaperResult",
    "RemovePaperResult",
    "ArchiveWorkspaceResult",
    "DeleteWorkspaceResult",
    "WorkspacePaperView",
    "WorkspaceStatus",
    "SchemaMode",
    "PaperSchemaStatus",
]