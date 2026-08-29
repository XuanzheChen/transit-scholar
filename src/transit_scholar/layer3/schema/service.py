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

The immutable Workspace Schema binding (schema_id / schema_version /
schema_hash, REQ-003) is enforced at every boundary:

- materialization validates the currently resolved ``SchemaDefinition``
  against the persisted binding BEFORE any L2S2 extraction/persistence
  (REQ-003 / AC-009..AC-011);
- every read path validates the persisted run itself — never
  ``current.json`` existence alone — and rejects runs whose recorded Schema
  identity is incompatible with the binding (REQ-004 / AC-012..AC-016);

A no-schema Workspace never exposes or materializes Workspace Schema content
(AC-007): every read/materialization entry point reports ``schema_disabled``
and there is no fallback read path to global or foreign instances.

T-001 (REQ-001): the governed current-run build snapshot
(``capture_current_run`` / ``capture_current_runs``) captures ONE validated
current persisted run per Paper with its exact ``SchemaInstance`` AND its
exact run identity (run_id / schema_id / schema_version / schema_hash /
current status) resolved together from that same run — ``current.json`` is
read once, the run is read explicitly by the captured run_id, and both are
validated against the immutable Workspace binding through the same boundary
every read uses, so no second current-run resolution can ever supply a
different identity (AC-001 / AC-002).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from transit_scholar.layer2.schema_extraction import get_schema_definition
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
    workspace_layout,
)
from transit_scholar.layer3.workspace.errors import (
    InvalidWorkspaceInputError,
    PaperNotMemberError,
    WorkspaceNotActiveError,
)
from transit_scholar.layer3.workspace.models import (
    PaperSchemaStatus,
    SchemaBinding,
    WorkspaceRecord,
)
from transit_scholar.layer3.workspace.service import WorkspaceService
from transit_scholar.layer3.workspace.schema_binding import (
    SCHEMA_MODE_BOUND,
    binding_for,
    matches_binding,
)

from .errors import (
    SchemaBindingMismatchError,
    SchemaDisabledError,
    SchemaMissingError,
)
from .snapshot import ValidatedCurrentSchemaRun

if TYPE_CHECKING:  # pragma: no cover - typing only
    from sqlalchemy.orm import Session

    from transit_scholar.layer2.schema_extraction.api import SchemaRunResult
    from transit_scholar.layer2.schema_extraction.models import (
        FieldResult,
        SchemaInstance,
    )
    from transit_scholar.layer2.schema_extraction.persistence import (
        SchemaRunStorage,
        StoredRun,
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


class PaperSchemaReadiness(BaseModel):
    """Derived per-Paper Workspace Schema readiness (REQ-004).

    ``status`` is ``ready`` only when the persisted Workspace current run is
    readable AND its Schema identity is fully compatible with the immutable
    Workspace binding; every other state is ``missing`` with the stable
    ``error_code`` explaining the boundary outcome (``schema_missing`` for
    absent/corrupt/unreadable content, ``schema_binding_mismatch`` for an
    incompatible identity). ``disabled`` (no-schema Workspaces) is derived by
    callers and never emitted here.
    """

    status: PaperSchemaStatus
    error_code: str | None = None


class WorkspaceSchemaService:
    """Workspace-bound Schema materialization and reads (REQ-004 / AC-006..07).

    ``data_root`` selects the derived-storage base for the Workspace Schema
    roots (defaults to the project settings data root; tests inject an
    isolated root). ``capture_current_run`` / ``capture_current_runs``
    additionally expose the governed current-run build snapshot (T-001 /
    REQ-001): one validated current persisted run carrying the exact
    ``SchemaInstance`` and the exact run identity together, so build content
    and fingerprint/provenance identity can never come from different runs.
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

        REQ-003: the currently resolved ``SchemaDefinition`` for the bound
        schema_id is verified against the persisted Workspace binding (exact
        schema_id, schema_version and deterministic schema_hash) BEFORE any
        L2S2 extraction or persistence; a definition that differs in version
        or content hash fails explicitly with ``schema_binding_mismatch`` and
        writes nothing (AC-009 / AC-010).

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
        binding = self.validate_binding(record)
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
        """Read the current (or a historical) Workspace-owned SchemaInstance.

        REQ-004: the persisted run (not merely ``current.json`` existence) is
        validated first — readable through the existing L2S2 integrity checks
        AND compatible with the immutable Workspace binding (AC-012..AC-014).
        """
        record = self._require_bound(workspace_id)
        self._require_member(workspace_id, paper_id)
        storage = self.layout(workspace_id).schema_storage()
        binding = record.schema_binding
        assert binding is not None
        stored = self.require_compatible_run(record, paper_id, storage, run_id=run_id)
        try:
            return get_schema(
                paper_id,
                binding.schema_id,
                run_id=stored.run_manifest.run_id,
                storage=storage,
            )
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
        """Read one field's result from the Workspace-owned current run.

        REQ-004: like ``get_instance``, the persisted run is validated for
        readability and binding compatibility before the L2S2 read.
        """
        record = self._require_bound(workspace_id)
        self._require_member(workspace_id, paper_id)
        storage = self.layout(workspace_id).schema_storage()
        binding = record.schema_binding
        assert binding is not None
        stored = self.require_compatible_run(record, paper_id, storage, run_id=run_id)
        try:
            return get_field(
                paper_id,
                binding.schema_id,
                field_id,
                run_id=stored.run_manifest.run_id,
                storage=storage,
            )
        except _MISSING_STORAGE_ERRORS as exc:
            raise SchemaMissingError(
                f"workspace {workspace_id!r} has no usable Schema content for "
                f"paper {paper_id!r}: {type(exc).__name__}: {exc}"
            ) from exc

    def validated_current_run_identities(
        self,
        workspace_id: str,
        paper_ids: list[str] | tuple[str, ...] | None = None,
    ) -> tuple[dict[str, dict[str, str] | None], dict[str, str]]:
        """Current Workspace Schema run identities derived ONLY from validated
        compatible current runs (REQ-002 / AC-007).

        Every member Paper's current run passes the SAME governance boundary
        used by reads (``require_compatible_run``): the persisted run must be
        readable through the normal L2S2 persistence integrity checks AND its
        Schema identity (schema_id / schema_version / schema_hash) must fully
        match the immutable Workspace binding — for both the current pointer
        and the persisted run manifest. Only then does its current-pointer
        identity contribute to the Wiki input fingerprint.

        A missing/corrupt/unreadable run, or a pointer/run-manifest that
        disagrees with the binding, yields ``None`` for that Paper and records
        the stable boundary code (``schema_missing`` /
        ``schema_binding_mismatch``) in the returned per-Paper error map.
        ``current.json`` existence (or a matching pointer) by itself NEVER
        authorizes an identity: the referenced run must still be readable and
        binding-compatible (AC-005..AC-007).
        """
        record = self._require_bound(workspace_id)
        storage = self.layout(workspace_id).schema_storage()
        binding = record.schema_binding
        assert binding is not None
        if paper_ids is None:
            paper_ids = tuple(
                membership.paper_id
                for membership in self.workspaces.list_memberships(workspace_id)
            )
        identities: dict[str, dict[str, str] | None] = {}
        invalid: dict[str, str] = {}
        for paper_id in sorted(paper_ids):
            try:
                self.require_compatible_run(record, paper_id, storage)
                pointer = storage.read_current(paper_id)
            except (SchemaMissingError, SchemaBindingMismatchError) as exc:
                identities[paper_id] = None
                invalid[paper_id] = exc.code
                continue
            except SchemaStorageError as exc:
                identities[paper_id] = None
                invalid[paper_id] = "schema_missing"
                continue
            identities[paper_id] = {
                "run_id": pointer.run_id,
                "schema_hash": pointer.schema_hash,
                "status": pointer.status,
            }
        return identities, invalid

    def current_run_identities(
        self,
        workspace_id: str,
        paper_ids: list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, dict[str, str] | None]:
        """Current Workspace Schema run identities for the given (or member)
        Papers — the File-backed identity input of the Base Wiki fingerprint.

        REQ-002: identities are derived ONLY from validated compatible current
        runs — each run passes the same ``require_compatible_run`` boundary
        used by reads (readable through the L2S2 integrity checks AND its
        schema_id / schema_version / schema_hash fully matching the immutable
        Workspace binding, for both the pointer and the persisted run
        manifest). A missing/corrupt/unreadable run, or a pointer/persisted
        run whose recorded identity disagrees with the Workspace binding, is
        not a usable current run identity for this Workspace and yields
        ``None`` — a current pointer alone never authorizes an identity
        (AC-007).
        """
        identities, _ = self.validated_current_run_identities(
            workspace_id, paper_ids
        )
        return identities

    def paper_schema_readiness(
        self,
        workspace_id: str,
        paper_ids: list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, PaperSchemaReadiness]:
        """Derived per-Paper Workspace Schema readiness (REQ-004).

        Read-only: for every Paper the persisted current run is loaded through
        the existing L2S2 read-back integrity checks and its Schema identity
        (schema_id / schema_version / schema_hash) is compared with the
        immutable Workspace binding. ``ready`` is reported ONLY for a readable,
        fully binding-compatible run — never from ``current.json`` existence
        alone (AC-012..AC-015).
        """
        record = self._require_bound(workspace_id)
        storage = self.layout(workspace_id).schema_storage()
        if paper_ids is None:
            paper_ids = tuple(
                membership.paper_id
                for membership in self.workspaces.list_memberships(workspace_id)
            )
        readiness: dict[str, PaperSchemaReadiness] = {}
        for paper_id in sorted(paper_ids):
            try:
                self.require_compatible_run(record, paper_id, storage)
            except SchemaBindingMismatchError as exc:
                readiness[paper_id] = PaperSchemaReadiness(
                    status="missing", error_code=exc.code
                )
            except SchemaMissingError as exc:
                readiness[paper_id] = PaperSchemaReadiness(
                    status="missing", error_code=exc.code
                )
            else:
                readiness[paper_id] = PaperSchemaReadiness(status="ready")
        return readiness

    # ------------------------------------------------------------------
    # governed current-run build snapshot (T-001 / REQ-001 / AC-001..02)
    # ------------------------------------------------------------------

    def capture_current_run(
        self, workspace_id: str, paper_id: str
    ) -> ValidatedCurrentSchemaRun:
        """Capture ONE validated current persisted run with instance+identity.

        REQ-001 (T-001): returns the exact ``SchemaInstance`` AND the exact
        run identity (run_id / schema_id / schema_version / schema_hash /
        current run status) resolved together from the SAME persisted run —
        capturing never resolves one current run for content and another for
        identity (AC-001 / AC-002 / C-001..C-003):

        1. ``current.json`` is read once and its run_id (A) is captured;
        2. the captured pointer A is validated against the immutable
           Workspace binding;
        3. the persisted run A is read explicitly by the captured run_id and
           validated through the same persistence-integrity + binding
           boundary every read uses (``require_compatible_run``);
        4. the ``SchemaInstance`` is read from that SAME run A.

        A concurrent ``current.json`` change after capture (pointer A -> B)
        cannot retroactively change this snapshot: instance and identity both
        stay A; the next freshness derivation compares the new governed B
        against the recorded A and derives stale (REQ-003).

        Binding-mismatch / missing / corrupt behavior is unchanged from every
        other governed read surface (REQ-004 / AC-009): a missing/unreadable
        pointer or referenced run raises ``SchemaMissingError``
        (``schema_missing``) and a pointer or persisted run whose Schema
        identity disagrees with the immutable Workspace binding raises
        ``SchemaBindingMismatchError`` (``schema_binding_mismatch``).
        """
        record = self._require_bound(workspace_id)
        self._require_member(workspace_id, paper_id)
        storage = self.layout(workspace_id).schema_storage()
        binding = record.schema_binding
        assert binding is not None
        # 1.: read current.json ONCE — the pointer IS the captured identity.
        try:
            pointer = storage.read_current(paper_id)
        except _MISSING_STORAGE_ERRORS as exc:
            raise SchemaMissingError(
                f"workspace {workspace_id!r} has no usable Schema content for "
                f"paper {paper_id!r}: {type(exc).__name__}: {exc}"
            ) from exc
        # 2.: the captured pointer must match the immutable Workspace binding.
        self._require_compatible_pointer(record, paper_id, pointer)
        # 3.: read the persisted run explicitly by the CAPTURED run_id and
        # validate persistence integrity + binding compatibility.
        stored = self.require_compatible_run(
            record, paper_id, storage, run_id=pointer.run_id
        )
        run = stored.run_manifest
        # 4.: the SchemaInstance comes from that SAME captured run.
        try:
            instance = get_schema(
                paper_id, binding.schema_id, run_id=run.run_id, storage=storage
            )
        except _MISSING_STORAGE_ERRORS as exc:
            raise SchemaMissingError(
                f"workspace {workspace_id!r} has no usable Schema content for "
                f"paper {paper_id!r}: {type(exc).__name__}: {exc}"
            ) from exc
        return ValidatedCurrentSchemaRun(
            paper_id=paper_id,
            run_id=pointer.run_id,
            instance=instance,
            schema_id=run.schema_id,
            schema_version=run.schema_version,
            schema_hash=run.schema_hash,
            status=pointer.status,
        )

    def capture_current_runs(
        self,
        workspace_id: str,
        paper_ids: list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, ValidatedCurrentSchemaRun]:
        """Bulk governed current-run snapshot capture (REQ-001 / AC-001..02).

        Captures every given (or every member) Paper's validated current run
        in deterministic sorted ``paper_id`` order, so a build can derive
        BOTH its L2S3 ``SchemaInstance`` inputs AND its fingerprint/
        provenance identities from the SAME snapshot collection — no second
        current-run resolution is needed for identity (AC-002 / C-002).

        Each Paper goes through the identical governed capture
        (``capture_current_run``), so the FIRST invalid run aborts the whole
        batch with its stable code — ``schema_missing`` (missing/corrupt/
        unreadable) or ``schema_binding_mismatch`` (binding-incompatible
        pointer or persisted run) — exactly like the per-Paper read paths
        (REQ-004 / AC-009): an incomplete batch is never returned, so no
        build can consume a partially governed set.
        """
        self._require_bound(workspace_id)
        if paper_ids is None:
            paper_ids = tuple(
                membership.paper_id
                for membership in self.workspaces.list_memberships(workspace_id)
            )
        return {
            paper_id: self.capture_current_run(workspace_id, paper_id)
            for paper_id in sorted(paper_ids)
        }

    # ------------------------------------------------------------------
    # binding validation (REQ-003 / REQ-004)
    # ------------------------------------------------------------------

    def validate_binding(self, record: WorkspaceRecord) -> SchemaBinding:
        """Verify the CURRENT SchemaDefinition against the Workspace binding.

        REQ-003 (materialization): resolves the current ``SchemaDefinition``
        for the bound schema_id through the existing L2S2 loader and derives
        the deterministic binding triple with the same canonical hashing used
        at Workspace creation. Raises ``SchemaBindingMismatchError`` when the
        definition cannot be resolved or its schema_id / schema_version /
        schema_hash differs from the persisted binding; the persisted binding
        is returned unchanged otherwise.
        """
        binding = record.schema_binding
        assert binding is not None  # guaranteed by bound-mode callers
        try:
            definition = get_schema_definition(binding.schema_id)
        except Exception as exc:  # noqa: BLE001 - any load failure is a mismatch
            raise SchemaBindingMismatchError(
                f"workspace {record.workspace_id!r} schema binding mismatch: "
                f"could not resolve the current SchemaDefinition for "
                f"schema_id {binding.schema_id!r} "
                f"({type(exc).__name__}: {exc})"
            ) from exc
        try:
            current_binding = binding_for(definition)
        except InvalidWorkspaceInputError as exc:
            raise SchemaBindingMismatchError(
                f"workspace {record.workspace_id!r} schema binding mismatch: "
                f"the current SchemaDefinition for schema_id "
                f"{binding.schema_id!r} is structurally invalid "
                f"({type(exc).__name__}: {exc})"
            ) from exc
        if not matches_binding(
            binding,
            schema_id=current_binding.schema_id,
            schema_version=current_binding.schema_version,
            schema_hash=current_binding.schema_hash,
        ):
            raise SchemaBindingMismatchError(
                f"workspace {record.workspace_id!r} schema binding mismatch: "
                f"the current SchemaDefinition for schema_id "
                f"{binding.schema_id!r} resolves to version "
                f"{current_binding.schema_version!r} / hash "
                f"{current_binding.schema_hash[:12]}..., but the Workspace "
                f"is immutably bound to version {binding.schema_version!r} / "
                f"hash {binding.schema_hash[:12]}... (REQ-003)"
            )
        return binding

    def require_compatible_run(
        self,
        record: WorkspaceRecord,
        paper_id: str,
        storage: "SchemaRunStorage",
        *,
        run_id: str | None = None,
    ) -> "StoredRun":
        """Load a persisted Workspace Schema run and prove it is usable.

        REQ-004: the run is usable only when (a) it is readable through the
        existing L2S2 read-back integrity checks and (b) its Schema identity
        matches the immutable Workspace binding:

        - ``current.json`` existence alone never makes a run usable (AC-012):
          a missing/corrupt/unreadable referenced run raises
          ``SchemaMissingError``;
        - the current pointer identity is checked first where it exists —
          including ``schema_hash``, which the normal L2S2 pointer metadata
          supplies (AC-014);
        - the persisted run manifest identity is then checked — schema_id /
          schema_version (AC-013) and schema_hash where the run metadata
          supplies it (AC-014);

        Any identity disagreement raises ``SchemaBindingMismatchError``, the
        single stable binding-mismatch code (AC-016).
        """
        binding = record.schema_binding
        assert binding is not None
        try:
            if run_id is None:
                pointer = storage.read_current(paper_id)
                self._require_compatible_pointer(record, paper_id, pointer)
                run_id = pointer.run_id
            stored = storage.read_run(paper_id, run_id)
        except SchemaBindingMismatchError:
            raise
        except _MISSING_STORAGE_ERRORS as exc:
            raise SchemaMissingError(
                f"workspace {record.workspace_id!r} has no usable Schema "
                f"content for paper {paper_id!r}: "
                f"{type(exc).__name__}: {exc}"
            ) from exc
        run = stored.run_manifest
        if not matches_binding(
            binding,
            schema_id=run.schema_id,
            schema_version=run.schema_version,
            schema_hash=run.schema_hash,
        ):
            raise SchemaBindingMismatchError(
                f"workspace {record.workspace_id!r} schema binding mismatch: "
                f"persisted run {run_id!r} for paper {paper_id!r} records "
                f"schema_id={run.schema_id!r} "
                f"schema_version={run.schema_version!r} "
                f"schema_hash={run.schema_hash[:12]}..., which does not match "
                f"the immutable Workspace binding "
                f"(schema_id={binding.schema_id!r} "
                f"schema_version={binding.schema_version!r} "
                f"schema_hash={binding.schema_hash[:12]}...) (REQ-004)"
            )
        return stored

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _require_compatible_pointer(
        self,
        record: WorkspaceRecord,
        paper_id: str,
        pointer: Any,
    ) -> None:
        """Validate one captured ``current.json`` pointer against the binding.

        Shared REQ-004 pointer gate used by every read path (via
        ``require_compatible_run``) and by the governed current-run build
        snapshot capture (``capture_current_run``): the pointer identity
        (schema_id / schema_version / schema_hash, including the schema_hash
        the normal L2S2 pointer metadata supplies, AC-014) must exactly match
        the immutable Workspace binding or the run is unusable with the
        stable ``schema_binding_mismatch`` code (AC-013/AC-014).
        """
        binding = record.schema_binding
        assert binding is not None
        if not matches_binding(
            binding,
            schema_id=pointer.schema_id,
            schema_version=pointer.schema_version,
            schema_hash=pointer.schema_hash,
        ):
            raise SchemaBindingMismatchError(
                f"workspace {record.workspace_id!r} schema binding "
                f"mismatch: current pointer for paper {paper_id!r} "
                f"records schema_id={pointer.schema_id!r} "
                f"schema_version={pointer.schema_version!r} "
                f"schema_hash={pointer.schema_hash[:12]}..., which "
                f"does not match the immutable Workspace binding "
                f"(schema_id={binding.schema_id!r} "
                f"schema_version={binding.schema_version!r} "
                f"schema_hash={binding.schema_hash[:12]}...) (REQ-004)"
            )

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


__all__ = ["WorkspaceSchemaService", "PaperSchemaReadiness"]