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

REQ-002 (T-002): the build captures ONE frozen complete authoritative build
snapshot (``WorkspaceSchemaService.capture_build_snapshot``,
``WorkspaceWikiSchemaBuildSnapshot`` — the exact binding-compatible
SchemaDefinition plus every member's validated current run) before L2S3 and
derives BOTH the L2S3 inputs AND the fingerprint/provenance identities from
that same snapshot (AC-002..AC-006 / C-001 / C-002). The Layer3-composed
``WorkspaceWikiBuildService`` receives the captured definition through
``schema_definition_loader`` and the captured per-Paper ``SchemaInstance``
values through ``schema_instance_loader`` (AC-004 / AC-005) — the default
current-definition/current-instance resolvers are never used for that build,
and no second authoritative resolution exists anywhere in the build, so Wiki
content and recorded identity always come from the same frozen snapshot
(AC-006 / AC-007). A concurrent ``current.json`` or plugin-definition change
after the capture cannot retroactively change that build's content or
identity; the next ``status()`` derives stale against the new governed
current (REQ-003 / AC-005..AC-008).

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
import re
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal

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
from .models import (
    WorkspaceWikiBuildOutcome,
    WorkspaceWikiCapability,
    WorkspaceWikiHit,
    WorkspaceWikiSearchResult,
    WorkspaceWikiStatus,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from sqlalchemy.orm import Session

    from transit_scholar.layer2.wiki.application import (
        CompositionFactory as CompositionFactory,
    )
    from transit_scholar.layer2.wiki.application import (
        PaperMetadataLoader as PaperMetadataLoader,
    )
    from transit_scholar.layer2.wiki.application import WorkspaceWikiBuildService
    from transit_scholar.layer2.wiki.models import WikiSearchResult
    from transit_scholar.layer2.wiki.service import WikiService
    from transit_scholar.layer3.schema.service import (
        WorkspaceSchemaService as WorkspaceSchemaService,
    )
    from transit_scholar.layer3.schema.snapshot import (
        WorkspaceWikiSchemaBuildSnapshot as WorkspaceWikiSchemaBuildSnapshot,
    )

class WorkspaceWikiService:
    """Workspace-bound Base Wiki build, status and read/search governance.

    ``data_root`` selects the derived-storage base (defaults to the project
    settings data root; tests inject an isolated root). Every composition wires
    the production L2S3 service with the Workspace-specific Wiki storage root,
    the captured SchemaDefinition (``schema_definition_loader``), and the
    already-governed captured Schema instances (``schema_instance_loader``).
    ``composition_factory`` overrides ONLY the production provider composition,
    while ``paper_metadata_loader`` can replace the non-Schema metadata source;
    the captured authoritative Schema loaders stay enforced for both seams.
    ``schemas`` injects the Layer3 Schema governance service (default: a
    ``WorkspaceSchemaService`` on the same ``session``/``data_root``); ``build``
    captures ONE frozen complete build snapshot through it
    (``capture_build_snapshot``: binding-compatible definition + every member's
    governed current run) before L2S3 consumption (REQ-001 / REQ-002).
    """

    def __init__(
        self,
        session: "Session",
        *,
        data_root: Path | str | None = None,
        workspaces: WorkspaceService | None = None,
        schemas: "WorkspaceSchemaService | None" = None,
        composition_factory: "CompositionFactory | None" = None,
        paper_metadata_loader: "PaperMetadataLoader | None" = None,
        agentic_wiki_store: object | None = None,
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
        # uses for reads; the Wiki build captures ONE frozen complete build
        # snapshot through it (binding-compatible definition + every member's
        # governed current run, carrying instance + identity) instead of
        # loading raw Workspace-local L2S2 content twice.
        self.schemas = schemas
        self._composition_factory = composition_factory
        if agentic_wiki_store is None:
            from transit_scholar.layer3.agentic_wiki import AgenticWikiStore  # noqa: PLC0415

            agentic_wiki_store = AgenticWikiStore()
        self.agentic_wiki_store = agentic_wiki_store
        if paper_metadata_loader is None:
            from transit_scholar.metadata.service import read_paper_metadata

            paper_metadata_loader = read_paper_metadata
        self._paper_metadata_loader = paper_metadata_loader

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

        REQ-001/REQ-002/REQ-003/REQ-004 (T-001/T-002): BEFORE the L2S3 build
        consumes anything, ONE frozen complete authoritative build snapshot is
        captured through the same ``WorkspaceSchemaService`` governance
        boundary used by Schema reads (``capture_build_snapshot``): the
        current ``SchemaDefinition`` is resolved and proven against the
        immutable Workspace binding by exact schema_id / schema_version /
        deterministic schema_hash (REQ-003), then every member Paper's current
        run is governed-captured — ``current.json`` read a single time, the
        captured pointer AND the persisted run both readable through the
        normal L2S2 integrity checks and fully compatible with the binding
        (AC-001..AC-003). A definition that no longer matches the binding
        (same id/version changed content hash, or changed version) fails with
        the stable ``schema_binding_mismatch`` code BEFORE any run capture and
        before L2S3 execution (AC-002 / AC-003); a missing/corrupt/unreadable
        run fails with ``schema_missing`` and a binding-incompatible pointer
        or persisted run with ``schema_binding_mismatch`` (AC-001..AC-003) —
        nothing is consumed by L2S3, no fallback to global/foreign content,
        and the existing Wiki/provenance is left untouched (C-007).

        Both the L2S3 inputs AND the fingerprint/provenance identities are
        derived from the SAME frozen snapshot (AC-002..AC-006 / C-001 / C-002):
        the Layer3-composed ``WorkspaceWikiBuildService`` receives the
        captured definition through ``schema_definition_loader`` and the
        captured per-Paper ``SchemaInstance`` values through
        ``schema_instance_loader`` (AC-004 / AC-005) — the default current
        definition/current-instance resolvers are never used for that build
        (REQ-002), so it is impossible to build Wiki content from definition/
        run B while recording definition/run A in fingerprint/provenance, and
        a concurrent ``current.json`` (A -> B) or plugin-definition change
        after the capture leaves this build's content and recorded identity at
        A (REQ-004 / AC-007). The next ``status()`` compares the governed
        current against the recorded identity and derives stale
        (REQ-003 / AC-005..AC-008).
        """
        record = self._require_active_bound(workspace_id)
        memberships = self.workspaces.list_memberships(workspace_id)
        context = derive_workspace_context(record, memberships)
        layout = workspace_layout(workspace_id, data_root=self.data_root)
        # REQ-001/REQ-002/REQ-003 (T-001/T-002): ONE frozen complete build
        # snapshot taken before L2S3 consumes anything.
        # WorkspaceSchemaService.capture_build_snapshot() resolves the current
        # SchemaDefinition and proves it against the immutable Workspace
        # binding (exact schema_id / schema_version / deterministic
        # schema_hash, REQ-003 — a same-id/version different content hash or
        # a different version rejects with the stable
        # ``schema_binding_mismatch`` code BEFORE any run is captured, AC-002 /
        # AC-003), then captures every member's validated current persisted
        # run through the governed per-run capture (current.json read ONCE;
        # the captured pointer and the persisted run validated for
        # readability + full binding compatibility; the exact SchemaInstance
        # and the exact run identity resolved together from the SAME run,
        # REQ-005). A missing/corrupt/unreadable or binding-incompatible run
        # aborts the whole capture with its stable code (``schema_missing`` /
        # ``schema_binding_mismatch``) before L2S3 ever sees the Paper
        # (AC-001..AC-003); no fallback is ever constructed and a failed
        # capture cannot disturb an existing Wiki/provenance (C-007).
        snapshot = self.schemas.capture_build_snapshot(
            workspace_id, context.paper_ids
        )
        # Both the L2S3 inputs and the fingerprint/provenance identities are
        # derived ONLY from the frozen snapshot (AC-003..AC-006 / C-001 /
        # C-002): no second authoritative definition/current-run resolution
        # exists anywhere in the build, so Wiki content and recorded identity
        # can never come from different runs, and a concurrent current.json /
        # plugin-definition change after the capture cannot retroactively
        # change this build's content or identity (REQ-003/REQ-004).
        identities = {
            paper_id: run.identity
            for paper_id, run in snapshot.runs_by_paper.items()
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
            workspace_id, layout, snapshot
        ).build_wiki_for_workspace(context)
        # REQ-006: the fingerprint and provenance identities derive from the
        # SAME frozen snapshot — its binding triple (matching BOTH the
        # captured definition and the Workspace binding, AC-010) and its
        # captured run identities (AC-009). No later definition/run
        # re-resolution can alter the recorded build identity.
        binding_identity = snapshot.binding_identity
        fingerprint = compute_wiki_input_fingerprint(
            workspace_id=record.workspace_id,
            schema_id=binding_identity["schema_id"],
            schema_version=binding_identity["schema_version"],
            schema_hash=binding_identity["schema_hash"],
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
        include_stale: bool = False,
    ) -> WorkspaceWikiSearchResult:
        """Search the Workspace-owned Base and promoted Agentic Wiki domains.

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
        base_result = wiki.search_wiki(query, limit=limit, mode=mode)
        base_hits = [
            WorkspaceWikiHit(
                type=hit.type,
                object_id=hit.object_id,
                title=hit.title,
                score=hit.score,
                snippet=hit.snippet,
                retrieval_mode=hit.retrieval_mode,
                source_kind="base_wiki",
            )
            for hit in base_result.hits
        ]
        agentic_hits = self._search_agentic_wiki(
            workspace_id,
            query,
            include_stale=include_stale,
        )
        combined = sorted(
            [*base_hits, *agentic_hits],
            key=lambda hit: (-hit.score, hit.source_kind, hit.object_id),
        )[:max(limit, 0)]
        return WorkspaceWikiSearchResult(
            status="degraded" if any(hit.lifecycle_status == "stale" for hit in combined) else "ok",
            hits=combined,
        )

    def _search_agentic_wiki(
        self,
        workspace_id: str,
        query: str,
        *,
        include_stale: bool,
    ) -> list[WorkspaceWikiHit]:
        entries = self.agentic_wiki_store.list(
            workspace_id,
            include_stale=include_stale,
        )
        query_terms = set(re.findall(r"\w+", query.casefold()))
        hits: list[WorkspaceWikiHit] = []
        for entry in entries:
            entry_terms = set(
                re.findall(r"\w+", f"{entry.title} {entry.content}".casefold())
            )
            score = len(query_terms & entry_terms) / max(len(query_terms), 1)
            hits.append(
                WorkspaceWikiHit(
                    type="entry",
                    object_id=entry.entry_id,
                    title=entry.title,
                    score=score,
                    snippet=entry.content,
                    source_kind="agentic_wiki",
                    lifecycle_status=entry.status if entry.status == "stale" else "active",
                )
            )
        return hits

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
        snapshot: "WorkspaceWikiSchemaBuildSnapshot",
    ) -> "WorkspaceWikiBuildService":
        """The L2S3 build service for this Workspace.

        The default composition receives ONLY the frozen build snapshot's
        captured inputs (REQ-002 / AC-004 / AC-005 / C-002): the captured
        ``SchemaDefinition`` through ``schema_definition_loader`` and the
        captured per-Paper ``SchemaInstance`` values through
        ``schema_instance_loader`` — never the default current-definition/
        current-instance resolvers, so no authoritative Schema source that
        could differ from the captured snapshot is re-resolved by that build
        (C-002). ``composition_factory`` replaces only the provider
        composition when injected; the captured-input loaders stay enforced.
        ``paper_metadata_loader`` may replace only the non-Schema metadata
        source and cannot affect either authoritative Schema loader.
        """
        from transit_scholar.layer2.wiki.application import (  # noqa: PLC0415
            WorkspaceWikiBuildService,
        )

        composition_kwargs = {}
        if self._composition_factory is not None:
            composition_kwargs["composition_factory"] = self._composition_factory
        return WorkspaceWikiBuildService(
            schema_definition_loader=lambda schema_id: snapshot.definition,
            schema_instance_loader=lambda paper_id, schema_id: (
                snapshot.runs_by_paper[paper_id].instance
            ),
            paper_metadata_loader=self._paper_metadata_loader,
            wiki_storage_root=layout.wiki_store_base,
            **composition_kwargs,
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


__all__ = ["WorkspaceWikiService"]
