"""Workspace-owned Base Wiki governance service (Layer3 Stage1, REQ-005).

A Base Wiki is owned by exactly one Workspace: its manifest, pages, entities,
links and indexes are persisted under that Workspace's storage boundary and
never reused as another Workspace's Base Wiki, even for overlapping Papers or
identical Schema bindings (AC-008). The L2S3 ``WorkspaceWikiBuildService`` /
``WikiStore`` / ``WikiService`` composition is reused through Workspace-specific
storage-root injection (AC-024); nothing here reimplements Wiki persistence
or search internals.

No-schema Workspaces report Base Wiki build capability as unsupported
(REQ-005 / AC-009). Freshness is derived from the deterministic input
fingerprint (REQ-007): the recorded fingerprint of the last successful build
is compared with one recomputed from the current Workspace identity, Schema
binding, membership, and current Workspace Schema run identities (AC-010 /
AC-011). No ``wiki_stale``/``wiki_ready`` boolean is ever persisted.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Literal

from transit_scholar.layer3.storage import (
    BuildProvenanceError,
    WorkspaceStorageLayout,
    compute_wiki_input_fingerprint,
    current_schema_run_identities,
    read_build_provenance,
    record_build_provenance,
    workspace_layout,
)
from transit_scholar.layer3.workspace.errors import WorkspaceNotActiveError
from transit_scholar.layer3.workspace.models import WorkspaceRecord
from transit_scholar.layer3.workspace.service import WorkspaceService
from transit_scholar.layer3.workspace.schema_binding import SCHEMA_MODE_BOUND

from .context import derive_workspace_context
from .errors import (
    WikiCorruptError,
    WikiMissingError,
    WikiStaleError,
    WikiUnsupportedError,
)
from .models import WorkspaceWikiBuildOutcome, WorkspaceWikiCapability, WorkspaceWikiStatus

if TYPE_CHECKING:  # pragma: no cover - typing only
    from sqlalchemy.orm import Session

    from transit_scholar.layer2.wiki.application import WorkspaceWikiBuildService
    from transit_scholar.layer2.wiki.models import WikiSearchResult
    from transit_scholar.layer2.wiki.service import WikiService

#: Callable receiving ``(workspace_id, layout)`` and returning the L2S3 build
#: service bound to this Workspace (injectable for deterministic tests).
BuildServiceFactory = Callable[
    [str, WorkspaceStorageLayout], "WorkspaceWikiBuildService"
]


class WorkspaceWikiService:
    """Workspace-bound Base Wiki build, status and read/search governance.

    ``data_root`` selects the derived-storage base (defaults to the project
    settings data root; tests inject an isolated root). ``build_service_factory``
    overrides the L2S3 ``WorkspaceWikiBuildService`` construction for
    deterministic tests (fake loaders/composition); the default factory wires
    the production L2S3 composition together with the Workspace-specific
    Schema storage and Wiki storage roots.
    """

    def __init__(
        self,
        session: "Session",
        *,
        data_root: Path | str | None = None,
        build_service_factory: BuildServiceFactory | None = None,
        workspaces: WorkspaceService | None = None,
    ) -> None:
        self.session = session
        self.data_root = data_root
        self.workspaces = workspaces or WorkspaceService(session)
        self._build_service_factory = build_service_factory

    # ------------------------------------------------------------------
    # capability (REQ-005 / AC-009)
    # ------------------------------------------------------------------

    def capability(self, workspace_id: str) -> WorkspaceWikiCapability:
        """Explicit capability summary for this Workspace.

        Build/read are supported only for an active, schema-bound Workspace
        with at least one member Paper; a no-schema Workspace reports
        unsupported (AC-009) with no fallback construction.
        """
        record = self.workspaces.get(workspace_id)
        status = self.status(workspace_id).status
        supported = (
            record.status == "active"
            and record.schema_mode == SCHEMA_MODE_BOUND
            and record.schema_binding is not None
            and bool(self.workspaces.list_memberships(workspace_id))
        )
        return WorkspaceWikiCapability(
            workspace_id=workspace_id,
            build_supported=supported,
            read_supported=supported and status in {"ready", "stale", "error"},
            status=status,
            reason=None if supported else "unsupported_schema_mode_or_empty_membership",
        )

    # ------------------------------------------------------------------
    # build (mutating; active + bound + members required)
    # ------------------------------------------------------------------

    def build(self, workspace_id: str) -> WorkspaceWikiBuildOutcome:
        """Build (or rebuild) the Base Wiki for an eligible schema-bound
        Workspace and record the input fingerprint provenance.

        The L2S3 ``WorkspaceWikiBuildService`` is composed with the
        Workspace-specific Wiki store root and a schema-instance loader bound
        to the Workspace-specific Schema storage root (AC-024 / REQ-006).
        """
        record = self._require_active_bound(workspace_id)
        memberships = self.workspaces.list_memberships(workspace_id)
        context = derive_workspace_context(record, memberships)
        layout = workspace_layout(workspace_id, data_root=self.data_root)
        try:
            prior = read_build_provenance(layout.wiki_dir)
        except BuildProvenanceError:
            # Unreadable provenance must not block a fresh rebuild; the new
            # provenance record overwrites it.
            prior = None
        # The existing L2S3 store cannot rebase a snapshot across input
        # contexts; when the recorded snapshot covers a different membership
        # (or is unreadable), rebuild into a fresh Workspace-owned boundary.
        if _snapshot_covers_different_inputs(layout, context.paper_ids):
            layout.delete_wiki_storage()
        build_result = self._build_service(workspace_id, layout).build_wiki_for_workspace(
            context
        )
        identities = current_schema_run_identities(
            layout.schema_storage(), context.paper_ids
        )
        binding = record.schema_binding
        assert binding is not None
        fingerprint = compute_wiki_input_fingerprint(
            workspace_id=record.workspace_id,
            schema_id=binding.schema_id,
            schema_version=binding.schema_version,
            schema_hash=binding.schema_hash,
            paper_ids=context.paper_ids,
            schema_run_identities=identities,
        )
        provenance = record_build_provenance(
            layout.wiki_dir,
            workspace_id=record.workspace_id,
            input_fingerprint=fingerprint,
            schema_runs=identities,
            build_status=build_result.manifest.build_status,
            build_revision=(prior.build_revision + 1 if prior is not None else 1),
        )
        return WorkspaceWikiBuildOutcome(
            result=build_result,
            fingerprint=fingerprint,
            provenance=provenance,
        )

    # ------------------------------------------------------------------
    # derived status (REQ-007 / AC-010 / AC-011)
    # ------------------------------------------------------------------

    def status(self, workspace_id: str) -> WorkspaceWikiStatus:
        """Derive the Base Wiki status from authoritative state only.

        Read-only: never mutates Schema/Wiki storage, DB or provenance, and
        never calls LLM/embedding providers. The recorded input fingerprint is
        recomputed from the current Workspace identity, Schema binding,
        membership and current Workspace Schema run identities:

        - fingerprint mismatch (or no recorded fingerprint) -> ``stale`` —
          the snapshot is observably non-current (AC-010);
        - provenance bound to a different Workspace -> ``error`` with
          ``workspace_mismatch`` (REQ-005 boundary: a foreign snapshot is
          never treated as this Workspace's Wiki);
        - exact fingerprint match -> the snapshot is then verified through the
          existing WikiStore integrity checks: intact -> ``ready`` (AC-011),
          otherwise ``error``.
        """
        record = self.workspaces.get(workspace_id)
        layout = workspace_layout(workspace_id, data_root=self.data_root)
        if record.schema_mode != SCHEMA_MODE_BOUND or record.schema_binding is None:
            return WorkspaceWikiStatus(
                workspace_id=record.workspace_id, status="unsupported"
            )
        memberships = self.workspaces.list_memberships(workspace_id)
        if not memberships:
            return WorkspaceWikiStatus(
                workspace_id=record.workspace_id,
                status="missing",
                error_code="empty_membership",
            )
        context = derive_workspace_context(record, memberships)
        if not (layout.wiki_dir / "manifest.json").is_file():
            return WorkspaceWikiStatus(
                workspace_id=record.workspace_id,
                status="missing",
                error_code="snapshot_missing",
            )
        try:
            provenance = read_build_provenance(layout.wiki_dir)
        except BuildProvenanceError as exc:
            return WorkspaceWikiStatus(
                workspace_id=record.workspace_id,
                status="error",
                error_code="build_provenance_unreadable",
            )
        identities = current_schema_run_identities(
            layout.schema_storage(), context.paper_ids
        )
        binding = record.schema_binding
        assert binding is not None
        fingerprint = compute_wiki_input_fingerprint(
            workspace_id=record.workspace_id,
            schema_id=binding.schema_id,
            schema_version=binding.schema_version,
            schema_hash=binding.schema_hash,
            paper_ids=context.paper_ids,
            schema_run_identities=identities,
        )
        if provenance is None:
            return WorkspaceWikiStatus(
                workspace_id=record.workspace_id,
                status="stale",
                fingerprint=fingerprint,
                error_code="no_recorded_fingerprint",
            )
        if provenance.workspace_id != record.workspace_id:
            return WorkspaceWikiStatus(
                workspace_id=record.workspace_id,
                status="error",
                error_code="workspace_mismatch",
                fingerprint=fingerprint,
            )
        if provenance.input_fingerprint != fingerprint:
            return WorkspaceWikiStatus(
                workspace_id=record.workspace_id,
                status="stale",
                fingerprint=fingerprint,
                recorded_fingerprint=provenance.input_fingerprint,
                build_revision=provenance.build_revision,
                built_at=_parse_built_at(provenance.built_at),
                error_code="input_fingerprint_mismatch",
            )
        # Inputs unchanged: the snapshot is this Workspace's own current
        # build; verify it through the existing WikiStore integrity checks.
        try:
            store = layout.wiki_store(context)
            manifest = store.get_manifest()
            store.list_pages()
        except Exception as exc:  # noqa: BLE001 - any store failure is corrupt
            return WorkspaceWikiStatus(
                workspace_id=record.workspace_id,
                status="error",
                error_code=getattr(exc, "code", "wiki_corrupt") or "wiki_corrupt",
            )
        return WorkspaceWikiStatus(
            workspace_id=record.workspace_id,
            status="ready",
            manifest_status=manifest.build_status,
            fingerprint=fingerprint,
            recorded_fingerprint=provenance.input_fingerprint,
            build_revision=provenance.build_revision,
            built_at=_parse_built_at(provenance.built_at),
        )

    # ------------------------------------------------------------------
    # read/search (bound Workspace boundary enforced; AC-008/AC-021)
    # ------------------------------------------------------------------

    def search(
        self,
        workspace_id: str,
        query: str,
        *,
        limit: int = 20,
        mode: Literal["lexical", "semantic"] = "lexical",
    ) -> "WikiSearchResult":
        """Search the Workspace-owned Base Wiki.

        Always resolves the store/service for THIS Workspace's Wiki root;
        missing snapshots raise ``WikiMissingError`` (AC-008: another
        Workspace's Wiki is never substituted) and stale snapshots raise
        ``WikiStaleError`` — an explicit degraded outcome (REQ-007
        recommendation), never silent access to non-current facts.
        """
        record = self.workspaces.get(workspace_id)
        layout = workspace_layout(workspace_id, data_root=self.data_root)
        if record.schema_mode != SCHEMA_MODE_BOUND or record.schema_binding is None:
            raise WikiUnsupportedError(
                f"workspace {workspace_id!r} has no Schema; Base Wiki reads are "
                "unsupported in Layer3 Stage1 (AC-009)"
            )
        memberships = self.workspaces.list_memberships(workspace_id)
        context = derive_workspace_context(record, memberships)
        derived = self.status(workspace_id)
        if derived.status == "missing":
            raise WikiMissingError(
                f"workspace {workspace_id!r} has no Base Wiki in its own "
                "storage boundary (AC-008)"
            )
        if derived.status == "stale":
            raise WikiStaleError(
                f"workspace {workspace_id!r} Base Wiki is stale: current "
                "inputs no longer match the recorded build fingerprint "
                "(REQ-007/AC-010)"
            )
        if derived.status == "error":
            raise WikiCorruptError(
                f"workspace {workspace_id!r} Base Wiki artifacts fail "
                f"integrity checks ({derived.error_code or 'unknown'})"
            )
        from transit_scholar.layer2.wiki.service import WikiService  # noqa: PLC0415

        store = layout.wiki_store(context)
        wiki = WikiService(context, store)
        return wiki.search_wiki(query, limit=limit, mode=mode)

    def get_wiki_service(self, workspace_id: str) -> "WikiService":
        """The L2S3 ``WikiService`` bound to the Workspace's own Wiki store.

        Called on every read operation; the caller must then validate the
        derived status for freshness before exposing results (see ``search``).
        """
        record = self.workspaces.get(workspace_id)
        layout = workspace_layout(workspace_id, data_root=self.data_root)
        if record.schema_mode != SCHEMA_MODE_BOUND or record.schema_binding is None:
            raise WikiUnsupportedError(
                f"workspace {workspace_id!r} has no Schema; Base Wiki reads are "
                "unsupported in Layer3 Stage1 (AC-009)"
            )
        memberships = self.workspaces.list_memberships(workspace_id)
        context = derive_workspace_context(record, memberships)
        from transit_scholar.layer2.wiki.service import WikiService  # noqa: PLC0415

        return WikiService(context, layout.wiki_store(context))

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _build_service(
        self, workspace_id: str, layout: WorkspaceStorageLayout
    ) -> "WorkspaceWikiBuildService":
        if self._build_service_factory is not None:
            return self._build_service_factory(workspace_id, layout)
        from transit_scholar.layer2.schema_extraction.api import get_schema  # noqa: PLC0415
        from transit_scholar.layer2.wiki.application import (  # noqa: PLC0415
            WorkspaceWikiBuildService,
        )

        schema_storage = layout.schema_storage()
        return WorkspaceWikiBuildService(
            schema_instance_loader=lambda paper_id, schema_id: get_schema(
                paper_id, schema_id, storage=schema_storage
            ),
            wiki_storage_root=layout.wiki_store_base,
        )

    def _require_active_bound(self, workspace_id: str) -> WorkspaceRecord:
        record = self.workspaces.get(workspace_id)
        if record.schema_mode != SCHEMA_MODE_BOUND or record.schema_binding is None:
            raise WikiUnsupportedError(
                f"workspace {workspace_id!r} has no bound Schema "
                f"(schema_mode={record.schema_mode!r}); Base Wiki build is "
                "unsupported in Layer3 Stage1 (REQ-005/AC-009)"
            )
        if record.status != "active":
            raise WorkspaceNotActiveError(
                f"workspace {workspace_id!r} is not active (status="
                f"{record.status!r}); Base Wiki build requires an active "
                "Workspace"
            )
        return record


def _parse_built_at(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo is not None else None


def _snapshot_covers_different_inputs(
    layout: WorkspaceStorageLayout, paper_ids: list[str]
) -> bool:
    """Whether the stored snapshot was built for a different membership.

    Reads only the persisted manifest's ``paper_ids`` (the recorded build
    inputs); an unreadable manifest is treated as needing a fresh rebuild.
    """
    manifest_path = layout.wiki_dir / "manifest.json"
    if not manifest_path.is_file():
        return False
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return True
    recorded = data.get("paper_ids") if isinstance(data, dict) else None
    if not isinstance(recorded, list) or not all(
        isinstance(item, str) for item in recorded
    ):
        return True
    return sorted(recorded) != sorted(paper_ids)


__all__ = ["WorkspaceWikiService", "BuildServiceFactory"]