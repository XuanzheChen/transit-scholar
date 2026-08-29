"""Workspace-owned Schema governance service (Layer3 Stage1, REQ-004).

Every persisted SchemaInstance, schema run, validation report, extraction
manifest, run manifest and current pointer produced for a Workspace is stored
under that Workspace's Schema storage boundary (derived from the persistent
Workspace identity) and is never reused as the persisted Schema content of
another Workspace — even for identical Paper+SchemaDefinition combinations
(AC-006).

Persistence is delegated to the existing L2S2 Package D public API
(``extract_schema`` / ``get_schema`` / ``get_field``) through the injected
Workspace-specific ``SchemaRunStorage`` (AC-024); this service only governs
the Workspace boundary (bound/none mode, membership, active lifecycle for
mutations) and never reimplements extraction or run storage.

A no-schema Workspace never exposes or materializes Workspace Schema content
(AC-007): every read/materialization entry point reports ``schema_disabled``
and there is no fallback read path to global or foreign instances.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from transit_scholar.layer2.schema_extraction.api import (
    SchemaFieldMissingError,
    SchemaIdMismatchError,
    extract_schema,
    get_field,
    get_schema,
)
from transit_scholar.layer2.schema_extraction.persistence import (
    SchemaCorruptRunError,
    SchemaCurrentNotFoundError,
    SchemaFileMissingError,
    SchemaHashMismatchError,
    SchemaInvalidJsonError,
    SchemaRunIdMismatchError,
    SchemaRunNotFoundError,
    SchemaStorageError,
)
from transit_scholar.layer3.storage import (
    WorkspaceStorageLayout,
    current_schema_run_identities,
    workspace_layout,
)
from transit_scholar.layer3.workspace.errors import (
    InvalidWorkspaceInputError,
    PaperNotMemberError,
    WorkspaceNotActiveError,
)
from transit_scholar.layer3.workspace.models import WorkspaceRecord
from transit_scholar.layer3.workspace.service import WorkspaceService
from transit_scholar.layer3.workspace.schema_binding import SCHEMA_MODE_BOUND

from .errors import SchemaDisabledError, SchemaMissingError

if TYPE_CHECKING:  # pragma: no cover - typing only
    from sqlalchemy.orm import Session

    from transit_scholar.layer2.schema_extraction.api import SchemaRunResult
    from transit_scholar.layer2.schema_extraction.models import (
        FieldResult,
        SchemaInstance,
    )
    from transit_scholar.layer2.schema_extraction.persistence import (
        SchemaRunStorage,
    )

#: L2S2 injection keys that would redirect persistence away from the
#: Workspace-specific storage boundary; always rejected.
_FORBIDDEN_STORAGE_INJECTIONS = frozenset({"storage", "storage_root"})

#: Storage failures that mean "no usable current Workspace Schema content".
_MISSING_STORAGE_ERRORS = (
    SchemaCurrentNotFoundError,
    SchemaRunNotFoundError,
    SchemaFileMissingError,
    SchemaInvalidJsonError,
    SchemaHashMismatchError,
    SchemaRunIdMismatchError,
    SchemaCorruptRunError,
    SchemaStorageError,
    SchemaIdMismatchError,
    SchemaFieldMissingError,
)


class WorkspaceSchemaService:
    """Workspace-bound Schema materialization and reads (REQ-004 / AC-006..07).

    ``data_root`` selects the derived-storage base for the Workspace Schema
    roots (defaults to the project settings data root; tests inject an
    isolated root).
    """

    def __init__(
        self,
        session: "Session",
        *,
        data_root: Path | str | None = None,
        workspaces: WorkspaceService | None = None,
    ) -> None:
        self.session = session
        self.data_root = data_root
        self.workspaces = workspaces or WorkspaceService(session)

    # ------------------------------------------------------------------
    # boundary resolution
    # ------------------------------------------------------------------

    def layout(self, workspace_id: str) -> WorkspaceStorageLayout:
        return workspace_layout(workspace_id, data_root=self.data_root)

    def schema_storage(self, workspace_id: str) -> "SchemaRunStorage":
        """The Workspace-specific Schema run storage (bound mode only).

        Raises ``SchemaDisabledError`` (never a fallback read) for Workspaces
        in ``none`` mode (AC-007).
        """
        self._require_bound(workspace_id)
        return self.layout(workspace_id).schema_storage()

    def is_schema_mode_bound(self, workspace_id: str) -> bool:
        record = self.workspaces.get(workspace_id)
        return record.schema_mode == SCHEMA_MODE_BOUND and record.schema_binding is not None

    # ------------------------------------------------------------------
    # materialization (mutating; active + bound + member required)
    # ------------------------------------------------------------------

    def materialize(
        self, workspace_id: str, paper_id: str, **l2s2_injections: Any
    ) -> "SchemaRunResult":
        """Extract/validate/persist a run for a member Paper into the
        Workspace-specific Schema root, reusing the L2S2 ``extract_schema``
        public API (AC-024).

        The Workspace-specific storage is always bound; callers may inject any
        other L2S2 option (``llm_client``, ``retrieval``, ``top_k``, ...) but
        ``storage``/``storage_root`` injection is rejected so the Workspace
        boundary cannot be redirected.
        """
        record = self._require_active_bound(workspace_id)
        self._require_member(workspace_id, paper_id)
        forbidden = sorted(set(l2s2_injections) & _FORBIDDEN_STORAGE_INJECTIONS)
        if forbidden:
            raise InvalidWorkspaceInputError(
                "Workspace Schema materialization binds the Workspace-specific "
                f"storage; injection of {', '.join(forbidden)} is rejected"
            )
        storage = self.layout(workspace_id).schema_storage()
        options = dict(l2s2_injections)
        options.setdefault("storage", storage)
        binding = record.schema_binding
        assert binding is not None  # guaranteed by _require_active_bound
        return extract_schema(paper_id, binding.schema_id, **options)

    # ------------------------------------------------------------------
    # reads (bound + member required; never fall back across Workspaces)
    # ------------------------------------------------------------------

    def get_instance(
        self,
        workspace_id: str,
        paper_id: str,
        *,
        run_id: str | None = None,
    ) -> "SchemaInstance":
        """Read the current (or a historical) Workspace-owned SchemaInstance."""
        record = self._require_bound(workspace_id)
        self._require_member(workspace_id, paper_id)
        storage = self.layout(workspace_id).schema_storage()
        binding = record.schema_binding
        assert binding is not None
        try:
            return get_schema(paper_id, binding.schema_id, run_id=run_id, storage=storage)
        except _MISSING_STORAGE_ERRORS as exc:
            raise SchemaMissingError(
                f"workspace {workspace_id!r} has no usable Schema content for "
                f"paper {paper_id!r}: {type(exc).__name__}: {exc}"
            ) from exc

    def get_field(
        self,
        workspace_id: str,
        paper_id: str,
        field_id: str,
        *,
        run_id: str | None = None,
    ) -> "FieldResult":
        """Read one field's result from the Workspace-owned current run."""
        record = self._require_bound(workspace_id)
        self._require_member(workspace_id, paper_id)
        storage = self.layout(workspace_id).schema_storage()
        binding = record.schema_binding
        assert binding is not None
        try:
            return get_field(
                paper_id, binding.schema_id, field_id, run_id=run_id, storage=storage
            )
        except _MISSING_STORAGE_ERRORS as exc:
            raise SchemaMissingError(
                f"workspace {workspace_id!r} has no usable Schema content for "
                f"paper {paper_id!r}: {type(exc).__name__}: {exc}"
            ) from exc

    def current_run_identities(
        self,
        workspace_id: str,
        paper_ids: list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, dict[str, str] | None]:
        """Current Workspace Schema run identities for the given (or member)
        Papers — the File-backed identity input of the Base Wiki fingerprint.

        Only the Workspace-specific current pointers are inspected; Papers
        without a current run yield ``None``.
        """
        self._require_bound(workspace_id)
        storage = self.layout(workspace_id).schema_storage()
        if paper_ids is None:
            paper_ids = tuple(
                membership.paper_id
                for membership in self.workspaces.list_memberships(workspace_id)
            )
        return current_schema_run_identities(storage, tuple(paper_ids))

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _require_bound(self, workspace_id: str) -> WorkspaceRecord:
        record = self.workspaces.get(workspace_id)
        if record.schema_mode != SCHEMA_MODE_BOUND or record.schema_binding is None:
            raise SchemaDisabledError(
                f"workspace {workspace_id!r} has no Schema "
                f"(schema_mode={record.schema_mode!r}); Workspace Schema access "
                "is disabled (REQ-004/AC-007)"
            )
        return record

    def _require_active_bound(self, workspace_id: str) -> WorkspaceRecord:
        record = self._require_bound(workspace_id)
        if record.status != "active":
            raise WorkspaceNotActiveError(
                f"workspace {workspace_id!r} is not active (status="
                f"{record.status!r}); Schema materialization requires an "
                "active Workspace"
            )
        return record

    def _require_member(self, workspace_id: str, paper_id: str) -> None:
        member_ids = {
            membership.paper_id
            for membership in self.workspaces.list_memberships(workspace_id)
        }
        if paper_id not in member_ids:
            raise PaperNotMemberError(
                f"paper {paper_id!r} is not a member of workspace {workspace_id!r}"
            )


__all__ = ["WorkspaceSchemaService"]