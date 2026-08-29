"""Workspace-owned Base Wiki governance service (Layer3 Stage1, REQ-005).

A Base Wiki is owned by exactly one Workspace: its manifest, pages, entities,
links and indexes are persisted under that Workspace's storage boundary and
never reused as another Workspace's Base Wiki, even for overlapping Papers or
identical Schema bindings (AC-008). The L2S3 ``WorkspaceWikiBuildService`` /
``WikiStore`` / ``WikiService`` composition is reused through Workspace-specific
storage-root injection (AC-024); nothing here reimplements Wiki persistence
or search internals.

REQ-001: Wiki construction never bypasses Workspace Schema governance. Every
member Paper's current Workspace Schema run is validated through the same
``WorkspaceSchemaService`` boundary used by Schema reads — readable through
the normal L2S2 integrity checks AND fully compatible with the immutable
Workspace binding (schema_id / schema_version / schema_hash) — BEFORE the
L2S3 build consumes any of it. A binding-incompatible pointer or persisted
run fails with the stable ``schema_binding_mismatch`` code and a
missing/corrupt/unreadable run with ``schema_missing``; there is no fallback
to global or foreign Schema content, and the L2S3 composition receives only
the already-governed compatible ``SchemaInstance`` values (or reads them
through the governed ``WorkspaceSchemaService`` read function).

REQ-002 (T-002): the build captures ONE governed current-run snapshot
collection (``WorkspaceSchemaService.capture_current_runs``,
``ValidatedCurrentSchemaRun``) before L2S3 and derives BOTH the
``SchemaInstance`` inputs (L2S3 ``instances_by_paper``) AND the
fingerprint/provenance identities from those same snapshot objects — there
is no second current-run resolution anywhere in the build (AC-002 / C-001),
so Wiki content and recorded identity always come from the same persisted
run (AC-003 / AC-004 / C-002). A concurrent ``current.json`` change after
the capture cannot retroactively change that build's recorded identity; the
next ``status()`` derives stale against the new governed current (REQ-003 /
AC-005..AC-008).

No-schema Workspaces report Base Wiki build capability as unsupported
(REQ-005 / AC-009). Freshness is derived from the deterministic input
fingerprint (REQ-007): the recorded fingerprint of the last successful build
is compared with one recomputed from the current Workspace identity, Schema
binding, membership, and current Workspace Schema run identities (AC-010 /
AC-011). REQ-002: those run identities are derived ONLY from validated
compatible current runs — each member Paper's current run must pass the same
``WorkspaceSchemaService`` governance boundary used by reads (readable
through the normal L2S2 integrity checks AND fully compatible with the
immutable Workspace binding, schema_id / schema_version / schema_hash)
before it contributes to the fingerprint, so a current pointer alone never
keeps a previously built Wiki ready/current once its referenced run becomes
missing, corrupt, unreadable or binding-incompatible (AC-005..AC-007).
``ready`` is a production-completeness state (REQ-001): the recorded
provenance and the persisted ``WikiManifest`` must both report
``build_status=complete``, the authoritative snapshot must pass the existing
WikiStore integrity checks, and the mandatory persistent vector index must
exist, be current for the same authoritative source fingerprint, and have
valid vector metadata with complete Page/Entity coverage (AC-001..AC-006).
No ``wiki_stale``/``wiki_ready`` boolean is ever persisted.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Literal

from transit_scholar.layer2.wiki.service import audit_vector_index_readonly
from transit_scholar.layer3.storage import (
    BuildProvenanceError,
    WorkspaceStorageLayout,
    compute_wiki_input_fingerprint,
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

    from transit_scholar.layer2.schema_extraction.models import SchemaInstance
    from transit_scholar.layer2.wiki.application import WorkspaceWikiBuildService
    from transit_scholar.layer2.wiki.models import WikiSearchResult
    from transit_scholar.layer2.wiki.service import WikiService
    from transit_scholar.layer3.schema.service import (
        WorkspaceSchemaService as WorkspaceSchemaService,
    )

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
    deterministic tests (fake loaders/composition); the default composition
    wires the production L2S3 service with the Workspace-specific Wiki storage
    root and the already-governed Schema instances. ``schemas`` injects the
    Layer3 Schema governance service (default: a ``WorkspaceSchemaService`` on
    the same ``session``/``data_root``); ``build`` captures every member
    Schema run through it (one governed current-run snapshot per Paper,
    ``capture_current_runs``) before L2S3 consumption (REQ-001 / REQ-002).
    """

    def __init__(
        self,
        session: "Session",
        *,
        data_root: Path | str | None = None,
        build_service_factory: BuildServiceFactory | None = None,
        workspaces: WorkspaceService | None = None,
        schemas: "WorkspaceSchemaService | None" = None,
    ) -> None:
        self.session = session
        self.data_root = data_root
        self.workspaces = workspaces or WorkspaceService(session)
        if schemas is None:
            from transit_scholar.layer3.schema.service import (  # noqa: PLC0415
                WorkspaceSchemaService,
            )

            schemas = WorkspaceSchemaService(session, data_root=data_root)
        # REQ-001/REQ-002: the SAME governance boundary WorkspaceSchemaService
        # uses for reads; the Wiki build captures member Schema runs through
        # it (one governed snapshot per Paper, carrying instance + identity)
        # instead of loading raw Workspace-local L2S2 content twice.
        self.schemas = schemas
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

        REQ-001/REQ-002 (T-001/T-002): BEFORE the L2S3 build consumes any
        member Paper's Schema run, every current Workspace Schema run is
        captured once through the same ``WorkspaceSchemaService`` governance
        boundary used by Schema reads (``capture_current_runs``): each Paper's
        ``current.json`` is read a single time, and the captured pointer AND
        the persisted run must both be readable through the normal L2S2
        integrity checks and fully compatible with the immutable Workspace
        binding (schema_id / schema_version / schema_hash). A
        missing/corrupt/unreadable run fails explicitly with the stable
        ``schema_missing`` code and a binding-incompatible pointer or
        persisted run with ``schema_binding_mismatch`` (AC-001..AC-003) —
        nothing is consumed by L2S3, no fallback to global/foreign content,
        and the existing snapshot is left untouched. Compatible runs build
        normally through the L2S3 ``WorkspaceWikiBuildService`` composed with
        the Workspace-specific Wiki store root and the already-governed
        compatible ``SchemaInstance`` values (AC-004 / AC-024 / REQ-006).

        Both the L2S3 input instances AND the fingerprint/provenance
        identities are derived from the SAME captured snapshot collection
        (AC-002 / AC-003 / AC-004 / C-001 / C-002) — the build never performs
        a second current-run resolution, so it is impossible to build Wiki
        content from run A while recording run B in fingerprint/provenance.
        A concurrent ``current.json`` change (A -> B) after the capture leaves
        this build's recorded identity at A; the next ``status()`` compares
        the governed current B against the recorded A and derives stale
        (REQ-003 / AC-005..AC-007).
        """
        record = self._require_active_bound(workspace_id)
        memberships = self.workspaces.list_memberships(workspace_id)
        context = derive_workspace_context(record, memberships)
        layout = workspace_layout(workspace_id, data_root=self.data_root)
        # REQ-001/REQ-002 (T-001/T-002): ONE governed capture of every member's
        # current Workspace Schema run, taken before L2S3 consumes anything.
        # WorkspaceSchemaService.capture_current_runs() reads each Paper's
        # current.json ONCE, validates the captured pointer and the persisted
        # run against the immutable Workspace binding (readability through the
        # normal L2S2 persistence integrity checks + full schema_id /
        # schema_version / schema_hash binding compatibility) and returns the
        # exact SchemaInstance AND the exact run identity together from the
        # SAME persisted run. A missing/corrupt/unreadable or
        # binding-incompatible run fails with the stable ``schema_missing`` /
        # ``schema_binding_mismatch`` codes before L2S3 ever sees the Paper
        # (AC-001..AC-003); no fallback is ever constructed.
        snapshots = self.schemas.capture_current_runs(
            workspace_id, context.paper_ids
        )
        # Both the L2S3 inputs and the fingerprint/provenance identities are
        # derived ONLY from the captured snapshot objects (AC-003 / AC-004 /
        # C-001 / C-002): no second current-run resolution exists anywhere in
        # the build, so Wiki content and recorded identity can never come from
        # different runs, and a concurrent current.json change after the
        # capture cannot retroactively change this build's identity (REQ-003).
        instances = {
            paper_id: snapshot.instance
            for paper_id, snapshot in snapshots.items()
        }
        identities = {
            paper_id: snapshot.identity
            for paper_id, snapshot in snapshots.items()
        }
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
        build_result = self._build_service(
            workspace_id, layout, instances
        ).build_wiki_for_workspace(context)
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
        never calls LLM/embedding providers or rebuilds indexes. The recorded
        input fingerprint is recomputed from the current Workspace identity,
        Schema binding, membership and the current Workspace Schema run
        identities — where every member run identity is derived ONLY after it
        passed the same ``WorkspaceSchemaService`` governance boundary used by
        reads (readable through the L2S2 integrity checks AND fully compatible
        with the immutable Workspace binding; REQ-002). A missing/corrupt/
        unreadable or binding-incompatible current run never contributes an
        identity, so a previously built Wiki ceases to be ready/current even
        when ``current.json`` bytes are unchanged (AC-005..AC-007):

        - validated-run fingerprint mismatch (or no recorded fingerprint) ->
          ``stale`` — the snapshot is observably non-current (AC-010);
          ``schema_input_invalid`` is the derived code when a member Schema
          run that contributed to the RECORDED build fails binding/readability
          validation (REQ-002 / AC-005..AC-007), ``input_fingerprint_mismatch``
          for a genuine input change;
        - provenance bound to a different Workspace -> ``error`` with
          ``workspace_mismatch`` (REQ-005 boundary: a foreign snapshot is
          never treated as this Workspace's Wiki);
        - recorded provenance ``build_status`` other than ``complete`` ->
          ``error`` with ``build_provenance_incomplete`` (REQ-001/AC-003);
        - exact fingerprint match -> the snapshot is then verified through the
          existing WikiStore integrity checks plus the production-completeness
          conditions of REQ-001: a ``partial``/``failed`` ``WikiManifest``
          build_status, or a missing/stale/incompatible mandatory persistent
          vector index, maps to ``error`` with a stable code (AC-001/AC-002/
          AC-004/AC-005); only a complete, current, structurally valid snapshot
          with a valid current vector index is ``ready`` (AC-006).
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
        # REQ-002: current-input identities are derived ONLY from validated
        # compatible current runs (never a raw current-pointer trust). Every
        # member Paper's current run must pass the same Workspace Schema
        # governance boundary used by reads — readable through the L2S2
        # persistence integrity checks AND fully compatible with the immutable
        # Workspace binding (schema_id / schema_version / schema_hash) — before
        # its identity enters the fingerprint; a missing/corrupt/unreadable or
        # binding-incompatible run yields ``None`` and records its stable code.
        identities, invalid_runs = self.schemas.validated_current_run_identities(
            workspace_id, context.paper_ids
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
        if provenance.input_fingerprint != fingerprint or invalid_runs:
            # AC-005..AC-007 (REQ-002): a member Paper's current run that
            # fails binding/readability validation invalidates freshness even
            # when current.json (and therefore the raw pointer identity) is
            # unchanged — the recorded build inputs are no longer validated as
            # current, so the previously built Wiki ceases to be ready/current.
            # ``schema_input_invalid`` is derived exactly when a run that
            # contributed to the RECORDED build (recorded identity present)
            # is no longer a validated current identity; genuine input changes
            # (membership changes, replacement current runs) keep the stable
            # ``input_fingerprint_mismatch`` code (AC-010).
            invalidated_run = any(
                identities.get(paper_id) is None
                and provenance.schema_runs.get(paper_id) is not None
                for paper_id in context.paper_ids
            )
            return WorkspaceWikiStatus(
                workspace_id=record.workspace_id,
                status="stale",
                fingerprint=fingerprint,
                recorded_fingerprint=provenance.input_fingerprint,
                build_revision=provenance.build_revision,
                built_at=_parse_built_at(provenance.built_at),
                error_code=(
                    "schema_input_invalid"
                    if invalidated_run
                    else "input_fingerprint_mismatch"
                ),
            )
        if provenance.build_status != "complete":
            # AC-003: an input-current snapshot is never ready when the
            # recorded provenance did not complete (even if every
            # authoritative JSON/JSONL source file is readable).
            return WorkspaceWikiStatus(
                workspace_id=record.workspace_id,
                status="error",
                error_code="build_provenance_incomplete",
                fingerprint=fingerprint,
                recorded_fingerprint=provenance.input_fingerprint,
                build_revision=provenance.build_revision,
                built_at=_parse_built_at(provenance.built_at),
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
        if manifest.build_status == "partial":
            # AC-001: a partial Manifest build never maps inputs-current to
            # ready.
            return WorkspaceWikiStatus(
                workspace_id=record.workspace_id,
                status="error",
                error_code="manifest_build_partial",
                manifest_status="partial",
                fingerprint=fingerprint,
                recorded_fingerprint=provenance.input_fingerprint,
                build_revision=provenance.build_revision,
                built_at=_parse_built_at(provenance.built_at),
            )
        if manifest.build_status == "failed":
            # AC-002: a failed Manifest build never maps inputs-current to
            # ready.
            return WorkspaceWikiStatus(
                workspace_id=record.workspace_id,
                status="error",
                error_code="manifest_build_failed",
                manifest_status="failed",
                fingerprint=fingerprint,
                recorded_fingerprint=provenance.input_fingerprint,
                build_revision=provenance.build_revision,
                built_at=_parse_built_at(provenance.built_at),
            )
        # The mandatory persistent vector index must exist and be current for
        # the SAME authoritative source fingerprint, with valid vector metadata
        # and complete Page/Entity vector coverage (REQ-001 / C-010). The L2S3
        # read-only helper audits it provider-free; the first issue's stable
        # code becomes the derived error_code (AC-004/AC-005). Any unexpected
        # store-level failure during the audit keeps the Wiki non-ready as a
        # stable integrity error instead of surfacing an implementation
        # exception (REQ-006 / AC-012 boundary containment).
        try:
            vector_issues = audit_vector_index_readonly(store)
        except Exception as exc:  # noqa: BLE001 - any audit failure is corrupt
            return WorkspaceWikiStatus(
                workspace_id=record.workspace_id,
                status="error",
                error_code=getattr(exc, "code", "wiki_corrupt") or "wiki_corrupt",
                manifest_status=manifest.build_status,
                fingerprint=fingerprint,
                recorded_fingerprint=provenance.input_fingerprint,
                build_revision=provenance.build_revision,
                built_at=_parse_built_at(provenance.built_at),
            )
        if vector_issues:
            return WorkspaceWikiStatus(
                workspace_id=record.workspace_id,
                status="error",
                error_code=vector_issues[0].code,
                manifest_status=manifest.build_status,
                fingerprint=fingerprint,
                recorded_fingerprint=provenance.input_fingerprint,
                build_revision=provenance.build_revision,
                built_at=_parse_built_at(provenance.built_at),
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
        self,
        workspace_id: str,
        layout: WorkspaceStorageLayout,
        instances: dict[str, "SchemaInstance"],
    ) -> "WorkspaceWikiBuildService":
        """The L2S3 build service for this Workspace.

        The default composition's ``schema_instance_loader`` returns only the
        already-governed compatible ``SchemaInstance`` values derived from the
        captured current-run snapshot collection
        (``capture_current_runs``, REQ-002 / AC-003 / C-002) — never raw L2S2
        ``get_schema()`` current reads that could bypass binding validation or
        resolve a different current run than the one whose identity is being
        recorded. The injectable factory keeps its ``(workspace_id, layout)``
        contract for deterministic test composition.
        """
        if self._build_service_factory is not None:
            return self._build_service_factory(workspace_id, layout)
        from transit_scholar.layer2.wiki.application import (  # noqa: PLC0415
            WorkspaceWikiBuildService,
        )

        return WorkspaceWikiBuildService(
            schema_instance_loader=lambda paper_id, schema_id: instances[paper_id],
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