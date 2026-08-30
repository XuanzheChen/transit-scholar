"""Workspace control-plane service (Layer3 Stage1).

Implements the persistent Workspace domain model (REQ-001), Workspace-to-Paper
membership (REQ-002), immutable Schema binding semantics (REQ-003) and the
database-backed control plane (REQ-006). Everything here is a mutation or read
of the authoritative SQLAlchemy state; Workspace-owned heavy derived artifacts
(Schema runs, Base Wiki) stay file-backed and are governed by later stages.

Service contract choices (documented for consumers and tests):

- ``add_paper`` is **idempotent**: adding an already-member Paper returns the
  existing membership with ``already_member=True`` and does not advance the
  Workspace revision. The ``(workspace_id, paper_id)`` unique constraint
  independently guarantees no duplicate rows can ever exist.
- ``remove_paper`` for a non-member raises ``PaperNotMemberError`` (an
  explicit revocation request is an error when there is nothing to revoke).
- Schema binding (mode/schema_id/schema_version/schema_hash) is immutable:
  ``rebind_schema`` always rejects, even for valid-looking inputs (AC-005).
- ``archive`` is **idempotent**: archiving an already-archived Workspace is a
  no-op without revision churn; memberships and Workspace-owned files are
  always preserved (AC-016) — only the lifecycle status changes.
- ``delete`` is **two-phase and idempotent**: the Workspace first transitions
  to the non-accessible ``deleting`` state, its memberships and the revision
  are persisted, and that revocation is then **committed durably BEFORE any
  destructive step** (REQ-009 / AC-017): ``flush()`` alone would let a process
  crash or caller rollback restore an active/visible Workspace whose files
  were already removed. Destructive cleanup of Workspace-owned Schema/Wiki
  storage runs only after that durable boundary, and the Workspace finally
  settles in the ``deleted`` tombstone state, also committed. Deleting an
  already-deleted Workspace is a no-op; a delete interrupted mid-flight
  resumes from ``deleting``. Global Paper/L2S1 records are never touched
  (AC-017 / C-009).
- ``revision`` starts at 1 at creation and is advanced by every authoritative
  mutation: membership add/remove and each lifecycle transition (archive;
  delete entering ``deleting``; delete settling in ``deleted``). Rejected or
  no-op operations never advance it.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from sqlalchemy import delete as sqlalchemy_delete
from sqlalchemy import select
from sqlalchemy.orm import Session

from transit_scholar.db.models import Paper, Workspace, WorkspacePaperMembership

from .errors import (
    InvalidWorkspaceInputError,
    PaperNotFoundError,
    PaperNotMemberError,
    SchemaBindingImmutableError,
    WorkspaceChangedError,
    WorkspaceNotFoundError,
    WorkspaceNotActiveError,
)
from .models import (
    AddPaperResult,
    ArchiveWorkspaceResult,
    CreateWorkspaceResult,
    DeleteWorkspaceResult,
    MembershipRecord,
    RemovePaperResult,
    SchemaBinding,
    WorkspaceRecord,
)
from .schema_binding import SCHEMA_MODE_BOUND, SCHEMA_MODE_NONE, binding_for

if TYPE_CHECKING:  # pragma: no cover - typing only
    from transit_scholar.layer2.schema_extraction.models import SchemaDefinition
    from transit_scholar.layer3.storage.paths import WorkspaceStorageLayout


def _new_id() -> str:
    return uuid.uuid4().hex


def _delete_derived_storage(workspace_id: str, layout: "WorkspaceStorageLayout") -> None:
    """Default \\ ``file_cleanup``: remove the Workspace's own derived boundary.

    Scoped strictly to this Workspace's derived storage directory; global
    Paper/L2S1 assets and other Workspaces' storage are never touched.
    """
    layout.delete()


def _ensure_non_empty(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidWorkspaceInputError(f"{label} must be a non-empty string")
    return value.strip()


class WorkspaceService:
    """Authoritative mutations and reads for the Workspace control plane."""

    def __init__(self, session: Session) -> None:
        self.session = session

    # ------------------------------------------------------------------
    # creation / reads (REQ-001)
    # ------------------------------------------------------------------

    def create(
        self,
        *,
        name: str,
        schema_definition: "SchemaDefinition | None" = None,
        workspace_id: str | None = None,
    ) -> CreateWorkspaceResult:
        """Create a Workspace in ``active`` lifecycle state.

        ``schema_definition`` selects the schema mode: a concrete
        ``SchemaDefinition`` creates a ``bound`` Workspace and persists the
        deterministic ``schema_id``/``schema_version``/``schema_hash`` triple
        (REQ-003 / AC-004); ``None`` creates a ``none`` Workspace with all
        three binding fields absent. The initial revision is 1.
        """
        name = _ensure_non_empty(name, "Workspace name")
        if schema_definition is not None:
            schema_mode = SCHEMA_MODE_BOUND
            binding: SchemaBinding | None = binding_for(schema_definition)
        else:
            schema_mode = SCHEMA_MODE_NONE
            binding = None
        if workspace_id is not None:
            workspace_id = _ensure_non_empty(workspace_id, "workspace_id")

        workspace = Workspace(
            id=workspace_id or _new_id(),
            name=name,
            status="active",
            schema_mode=schema_mode,
            schema_id=binding.schema_id if binding else None,
            schema_version=binding.schema_version if binding else None,
            schema_hash=binding.schema_hash if binding else None,
            revision=1,
        )
        self.session.add(workspace)
        self.session.flush()
        # Fetch server-side defaults (created_at/updated_at) so the returned
        # snapshot is complete and reproducible.
        self.session.refresh(workspace)
        return CreateWorkspaceResult(workspace=WorkspaceRecord.from_row(workspace))

    def get(self, workspace_id: str) -> WorkspaceRecord:
        """Read the authoritative Workspace state (AC-001)."""
        return WorkspaceRecord.from_row(self._get(workspace_id))

    def require_active(self, workspace_id: str) -> WorkspaceRecord:
        """Read a Workspace and require its active lifecycle state.

        Higher layers use this guard when creation must be bound to the current
        Workspace boundary without reproducing Workspace lifecycle semantics.
        """
        return WorkspaceRecord.from_row(self._get_active(workspace_id))

    def list_workspaces(self) -> list[WorkspaceRecord]:
        """All Workspaces in deterministic (created_at, id) order."""
        rows = self.session.execute(
            select(Workspace).order_by(Workspace.created_at, Workspace.id)
        ).scalars().all()
        return [WorkspaceRecord.from_row(row) for row in rows]

    def schema_binding(self, workspace_id: str) -> SchemaBinding | None:
        """Current immutable Schema binding, or ``None`` for none mode."""
        workspace = self._get(workspace_id)
        if workspace.schema_mode != SCHEMA_MODE_BOUND:
            return None
        return SchemaBinding(
            schema_id=workspace.schema_id,
            schema_version=workspace.schema_version,
            schema_hash=workspace.schema_hash,
        )

    # ------------------------------------------------------------------
    # membership (REQ-002)
    # ------------------------------------------------------------------

    def add_paper(self, workspace_id: str, paper_id: str) -> AddPaperResult:
        """Add a global Paper to the Workspace (idempotent, AC-002/AC-003).

        Succeeds regardless of the Paper's L2S1/Schema/Wiki derived-asset
        readiness (AC-014 contract kept for later stages): membership only
        records Workspace visibility of the global Paper identity.
        """
        workspace = self._get_active(workspace_id)
        paper_id = _ensure_non_empty(paper_id, "paper_id")
        paper = self.session.get(Paper, paper_id)
        if paper is None:
            raise PaperNotFoundError(
                f"global paper {paper_id!r} does not exist; cannot add membership"
            )
        membership = self.session.execute(
            select(WorkspacePaperMembership).where(
                WorkspacePaperMembership.workspace_id == workspace.id,
                WorkspacePaperMembership.paper_id == paper.id,
            )
        ).scalar_one_or_none()
        if membership is not None:
            return AddPaperResult(
                workspace=WorkspaceRecord.from_row(workspace),
                membership=MembershipRecord.from_row(membership),
                already_member=True,
            )
        membership = WorkspacePaperMembership(
            workspace_id=workspace.id, paper_id=paper.id
        )
        self.session.add(membership)
        self._advance_revision(workspace)
        self.session.flush()
        return AddPaperResult(
            workspace=WorkspaceRecord.from_row(workspace),
            membership=MembershipRecord.from_row(membership),
            already_member=False,
        )

    def remove_paper(self, workspace_id: str, paper_id: str) -> RemovePaperResult:
        """Revoke Workspace visibility of a Paper (REQ-009 visibility contract).

        The authoritative membership row is removed first, so access is
        revoked immediately and independently of any Workspace-owned derived
        file cleanup. Raises ``PaperNotMemberError`` when the Paper is not a
        current member.
        """
        workspace = self._get_active(workspace_id)
        paper_id = _ensure_non_empty(paper_id, "paper_id")
        membership = self.session.execute(
            select(WorkspacePaperMembership).where(
                WorkspacePaperMembership.workspace_id == workspace.id,
                WorkspacePaperMembership.paper_id == paper_id,
            )
        ).scalar_one_or_none()
        if membership is None:
            raise PaperNotMemberError(
                f"paper {paper_id!r} is not a member of workspace {workspace.id!r}"
            )
        self.session.delete(membership)
        self._advance_revision(workspace)
        self.session.flush()
        return RemovePaperResult(
            workspace=WorkspaceRecord.from_row(workspace), paper_id=paper_id
        )

    def list_memberships(self, workspace_id: str) -> list[MembershipRecord]:
        """Current visible Paper membership in deterministic paper_id order."""
        workspace = self._get(workspace_id)
        rows = self.session.execute(
            select(WorkspacePaperMembership)
            .where(WorkspacePaperMembership.workspace_id == workspace.id)
            .order_by(WorkspacePaperMembership.paper_id)
        ).scalars().all()
        return [MembershipRecord.from_row(row) for row in rows]

    # ------------------------------------------------------------------
    # lifecycle (REQ-009 / AC-016 / AC-017)
    # ------------------------------------------------------------------

    def archive(self, workspace_id: str) -> ArchiveWorkspaceResult:
        """Archive an active Workspace (idempotent, REQ-009 / AC-016).

        Only the lifecycle status changes: memberships and all Workspace-owned
        files are preserved, while normal active knowledge access becomes
        rejected with ``workspace_not_active``. Archiving an already-archived
        Workspace is a no-op; archiving a Workspace in ``deleting``/``deleted``
        state is rejected explicitly.
        """
        workspace = self._get(workspace_id)
        if workspace.status in ("deleting", "deleted"):
            raise WorkspaceNotActiveError(
                f"workspace {workspace.id!r} cannot be archived (status="
                f"{workspace.status!r}); lifecycle mutations require an active "
                "or archived Workspace"
            )
        if workspace.status == "archived":
            return ArchiveWorkspaceResult(
                workspace=WorkspaceRecord.from_row(workspace),
                already_archived=True,
            )
        workspace.status = "archived"
        self._advance_revision(workspace)
        self.session.flush()
        return ArchiveWorkspaceResult(workspace=WorkspaceRecord.from_row(workspace))

    def delete(
        self,
        workspace_id: str,
        *,
        data_root: Path | str | None = None,
        file_cleanup: Callable[[str, "WorkspaceStorageLayout"], None] | None = None,
    ) -> DeleteWorkspaceResult:
        """Delete a Workspace (idempotent, REQ-009 / AC-017 / C-009).

        Two-phase lifecycle: the Workspace first transitions to the
        non-accessible ``deleting`` state with the revision advanced, and
        that state together with the membership revocation is COMMITTED
        durably BEFORE any destructive step (a flush alone is not a durable
        transaction boundary: another session could not rely on observing
        the revocation, and a process crash or caller rollback after file
        deletion would restore an active/visible Workspace whose files were
        already removed). Only then are its memberships and Workspace-owned
        Schema/Wiki storage removed, and it finally settles in the
        ``deleted`` tombstone state, which is likewise committed. Global
        Paper records and L2S1 assets are never touched (C-009).

        ``file_cleanup`` receives ``(workspace_id, layout)`` and is invoked
        after the durable revocation; when omitted, the Workspace's entire
        derived-storage boundary under ``data_root`` (or the default Layer3
        root) is removed. Deleting an already-deleted Workspace is a no-op;
        a delete interrupted in the ``deleting`` state completes the same
        sequence.
        """
        workspace = self._get(workspace_id)
        if workspace.status == "deleted":
            return DeleteWorkspaceResult(
                workspace=WorkspaceRecord.from_row(workspace),
                already_deleted=True,
            )
        if workspace.status != "deleting":
            workspace.status = "deleting"
            self._advance_revision(workspace)
            self.session.flush()
        self.session.execute(
            sqlalchemy_delete(WorkspacePaperMembership).where(
                WorkspacePaperMembership.workspace_id == workspace.id
            )
        )
        self.session.flush()

        # Durable revocation boundary: the non-accessible ``deleting`` state
        # and the membership revocation are COMMITTED before any destructive
        # work begins. From this point onward the Workspace is durably
        # non-accessible and the deletion is resumable after any failure, so
        # cleanup can never run against a state that a crash or caller
        # rollback could resurrect as active/visible.
        self.session.commit()

        # The commit expired the ORM state; re-load the authoritative row.
        workspace = self._get(workspace_id)

        from transit_scholar.layer3.storage.paths import (  # noqa: PLC0415
            workspace_layout,
        )

        layout = workspace_layout(workspace.id, data_root=data_root)
        cleanup = file_cleanup or _delete_derived_storage
        cleanup(workspace.id, layout)

        workspace.status = "deleted"
        self._advance_revision(workspace)
        self.session.flush()
        # Durable tombstone: the completed deletion is committed so a later
        # caller rollback cannot resurrect an already-cleanup Workspace.
        self.session.commit()
        return DeleteWorkspaceResult(workspace=WorkspaceRecord.from_row(workspace))

    # ------------------------------------------------------------------
    # schema binding immutability (REQ-003 / AC-005)
    # ------------------------------------------------------------------

    def rebind_schema(
        self,
        workspace_id: str,
        *,
        schema_mode: str | None = None,
        schema_definition: "SchemaDefinition | None" = None,
    ) -> None:
        """Reject any Schema binding change in Layer3 Stage1 (AC-005).

        Switching schema mode, schema_id, schema_version, or schema_hash of
        an existing Workspace is unsupported and MUST NOT mutate persisted
        state. The Workspace existence is still validated first so callers
        distinguish ``workspace_not_found`` from ``schema_binding_immutable``.
        """
        self._get(workspace_id)
        requested = schema_mode or (
            SCHEMA_MODE_BOUND if schema_definition is not None else None
        )
        raise SchemaBindingImmutableError(
            "Schema binding is immutable in Layer3 Stage1: cannot switch "
            f"Workspace {workspace_id!r} to mode "
            f"{requested or 'another binding'} (AC-005)"
        )

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _get(self, workspace_id: str) -> Workspace:
        workspace_id = _ensure_non_empty(workspace_id, "workspace_id")
        workspace = self.session.get(Workspace, workspace_id)
        if workspace is None:
            raise WorkspaceNotFoundError(
                f"workspace {workspace_id!r} does not exist"
            )
        return workspace

    def _get_active(self, workspace_id: str) -> Workspace:
        workspace = self._get(workspace_id)
        if workspace.status != "active":
            raise WorkspaceNotActiveError(
                f"workspace {workspace.id!r} is not active (status="
                f"{workspace.status!r}); membership mutations require an "
                "active Workspace"
            )
        return workspace

    def _advance_revision(self, workspace: Workspace) -> None:
        """Advance the monotonic revision on an authoritative mutation."""
        workspace.revision += 1


__all__ = ["WorkspaceService"]
