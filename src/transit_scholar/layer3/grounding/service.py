"""Read-only Workspace Grounding service (Layer3 Stage1, REQ-007/REQ-008).

``WorkspaceGroundingService.ground`` resolves a Workspace into a normalized,
side-effect-free ``GroundedWorkspace`` snapshot derived from authoritative
sources only:

- the database control plane (Workspace identity/status/revision/schema
  binding and current Paper membership);
- the global L2S1 consumable assets (canonical parse pointer + retrieval
  index), inspected through the existing ``L2S1EvidenceDelegate``;
- the Workspace-owned Schema current pointers (injected into the existing
  L2S2 ``SchemaRunStorage``, read-only);
- the Workspace-owned Base Wiki artifacts and recorded build provenance
  (derived freshness through ``WorkspaceWikiService.status``, REQ-007).

Grounding inspects and normalizes existing state ONLY (C-007): it never calls
an LLM or embedding provider, never builds retrieval indexes, never extracts
Schema content, never builds/rebuilds a Wiki, and never mutates Schema runs,
current pointers, Wiki snapshots/indexes, memberships or Workspace lifecycle
state (AC-013). It MAY derive recommended actions for upper-layer planning,
but those are only reported inside the snapshot — never executed.

Every lower-layer inspection goes through injectable collaborators
(``workspaces`` / ``schemas`` / ``wiki`` / ``evidence``) so tests can
substitute recording fakes and prove the read-only property at the seam.
Determinism: membership and per-Paper results are ordered deterministically
and no grounding-time timestamps are added, so identical state grounds to an
identical snapshot.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from transit_scholar.db.models import Paper

from transit_scholar.layer3.knowledge.gateway import L2S1EvidenceDelegate
from transit_scholar.layer3.wiki.service import WorkspaceWikiService
from transit_scholar.layer3.workspace.models import (
    PaperSchemaStatus,
    WorkspaceRecord,
)
from transit_scholar.layer3.workspace.schema_binding import SCHEMA_MODE_BOUND
from transit_scholar.layer3.workspace.service import WorkspaceService

from .models import (
    GroundedPaper,
    GroundedWorkspace,
    RecommendedAction,
    SchemaCoverage,
    WorkspaceCapabilities,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from transit_scholar.layer3.schema.service import WorkspaceSchemaService

#: Stable recommended-action codes (REQ-008: reported only, never executed).
ACTION_MATERIALIZE_SCHEMA_RUNS = "materialize_schema_runs"
ACTION_BUILD_BASE_WIKI = "build_base_wiki"
ACTION_REBUILD_BASE_WIKI = "rebuild_base_wiki"
ACTION_REPAIR_BASE_WIKI = "repair_base_wiki"


class WorkspaceGroundingService:
    """Read-only resolver of one Workspace into a normalized snapshot.

    ``data_root`` selects the derived-storage base for the default Schema/Wiki
    collaborators (defaults to the project settings data root; tests inject an
    isolated root). All collaborators default to the production services; each
    may be replaced with a fake for deterministic tests.
    """

    def __init__(
        self,
        session: Session,
        *,
        data_root: Path | str | None = None,
        workspaces: WorkspaceService | None = None,
        schemas: "WorkspaceSchemaService | None" = None,
        wiki: WorkspaceWikiService | None = None,
        evidence: L2S1EvidenceDelegate | None = None,
    ) -> None:
        self.session = session
        self.data_root = data_root
        self.workspaces = workspaces or WorkspaceService(session)
        if schemas is None:
            from transit_scholar.layer3.schema.service import (  # noqa: PLC0415
                WorkspaceSchemaService,
            )

            schemas = WorkspaceSchemaService(session, data_root=data_root)
        if wiki is None:
            wiki = WorkspaceWikiService(session, data_root=data_root)
        self.schemas = schemas
        self.wiki = wiki
        self.evidence = evidence or L2S1EvidenceDelegate()

    # ------------------------------------------------------------------
    # grounding (read-only, REQ-008 / AC-012)
    # ------------------------------------------------------------------

    def ground(self, workspace_id: str) -> GroundedWorkspace:
        """Resolve the Workspace into the current normalized snapshot.

        Raises ``WorkspaceNotFoundError`` when no Workspace exists with the
        requested identifier. Every other Workspace lifecycle state (active,
        archived, deleting, deleted) is reported in the snapshot rather than
        rejected: Grounding describes state, and the capability summary
        reflects which knowledge operations are currently available.
        """
        record = self.workspaces.get(workspace_id)  # workspace_not_found
        memberships = self.workspaces.list_memberships(workspace_id)
        paper_ids = sorted({membership.paper_id for membership in memberships})
        schema_status = self._schema_status_by_paper(record, paper_ids)
        visible_papers = [
            self._paper_snapshot(record, paper_id, schema_status[paper_id])
            for paper_id in paper_ids
        ]
        coverage = self._schema_coverage(record, paper_ids, schema_status)
        base_wiki = self.wiki.status(workspace_id)
        capabilities = self._capabilities(
            record, paper_ids, visible_papers, coverage, base_wiki
        )
        actions = self._recommended_actions(record, paper_ids, schema_status, base_wiki)
        return GroundedWorkspace(
            workspace_id=record.workspace_id,
            name=record.name,
            revision=record.revision,
            status=record.status,
            schema_mode=record.schema_mode,
            schema_binding=record.schema_binding,
            member_paper_ids=paper_ids,
            visible_papers=visible_papers,
            schema_coverage=coverage,
            base_wiki=base_wiki,
            capabilities=capabilities,
            recommended_actions=actions,
        )

    # ------------------------------------------------------------------
    # internals (read-only derivations)
    # ------------------------------------------------------------------

    def _schema_status_by_paper(
        self, record: WorkspaceRecord, paper_ids: list[str]
    ) -> dict[str, PaperSchemaStatus]:
        """Per-Paper Workspace Schema status (disabled/missing/ready).

        For a no-schema Workspace every Paper is ``disabled`` and the
        Workspace Schema storage is never inspected (AC-007: no-schema
        Workspaces MUST NOT materialize/expose Schema content, not even by
        inspection fallback). For bound mode only the Workspace's own current
        pointers are read (AC-020).
        """
        if record.schema_mode != SCHEMA_MODE_BOUND or record.schema_binding is None:
            return {paper_id: "disabled" for paper_id in paper_ids}
        identities = self.schemas.current_run_identities(
            record.workspace_id, paper_ids
        )
        return {
            paper_id: ("ready" if identities.get(paper_id) else "missing")
            for paper_id in paper_ids
        }

    def _paper_snapshot(
        self,
        record: WorkspaceRecord,
        paper_id: str,
        schema_status: PaperSchemaStatus,
    ) -> GroundedPaper:
        paper = self.session.get(Paper, paper_id)
        return GroundedPaper(
            workspace_id=record.workspace_id,
            paper_id=paper_id,
            title=paper.title if paper is not None else None,
            paper_status=paper.status if paper is not None else "active",
            l2s1_ready=self.evidence.l2s1_ready(paper_id),
            schema_status=schema_status,
        )

    def _schema_coverage(
        self,
        record: WorkspaceRecord,
        paper_ids: list[str],
        schema_status: dict[str, PaperSchemaStatus],
    ) -> SchemaCoverage:
        total = len(paper_ids)
        if record.schema_mode != SCHEMA_MODE_BOUND or record.schema_binding is None:
            # No-schema Workspace: Schema coverage is disabled. ``ready`` and
            # ``missing`` do not apply (no run can ever exist); both are 0 and
            # the status carries the semantics (AC-007).
            return SchemaCoverage(
                workspace_id=record.workspace_id,
                total=total,
                ready=0,
                missing=0,
                status="disabled",
            )
        ready = sum(
            1 for status in schema_status.values() if status == "ready"
        )
        missing = total - ready
        if total == 0:
            status = "empty"
        elif ready == total:
            status = "complete"
        elif ready == 0:
            status = "missing"
        else:
            status = "partial"
        return SchemaCoverage(
            workspace_id=record.workspace_id,
            total=total,
            ready=ready,
            missing=missing,
            status=status,
        )

    def _capabilities(
        self,
        record: WorkspaceRecord,
        paper_ids: list[str],
        visible_papers: list[GroundedPaper],
        coverage: SchemaCoverage,
        base_wiki: "WorkspaceWikiStatus",
    ) -> WorkspaceCapabilities:
        active = record.status == "active"
        bound = (
            record.schema_mode == SCHEMA_MODE_BOUND
            and record.schema_binding is not None
        )
        has_members = bool(paper_ids)
        wiki_build = active and bound and has_members
        return WorkspaceCapabilities(
            workspace_id=record.workspace_id,
            knowledge_access=active,
            paper_access=active,
            evidence_access=active and has_members,
            schema_read=active and bound,
            schema_materialization=active and bound,
            wiki_build=wiki_build,
            wiki_read=wiki_build and base_wiki.status in {"ready", "stale", "error"},
            evidence_ready_papers=sum(1 for paper in visible_papers if paper.l2s1_ready),
            schema_ready_papers=coverage.ready,
        )

    def _recommended_actions(
        self,
        record: WorkspaceRecord,
        paper_ids: list[str],
        schema_status: dict[str, PaperSchemaStatus],
        base_wiki: "WorkspaceWikiStatus",
    ) -> list[RecommendedAction]:
        """Derive repair/next-step suggestions without executing any of them.

        Actions are only reported for an active, schema-bound Workspace with
        member Papers; a no-schema Workspace never receives Schema/Wiki
        actions (REQ-005/AC-009: nothing to fabricate or borrow), and a
        non-active Workspace requires a control-plane decision first.
        """
        actions: list[RecommendedAction] = []
        bound = (
            record.schema_mode == SCHEMA_MODE_BOUND
            and record.schema_binding is not None
        )
        if record.status != "active" or not bound or not paper_ids:
            return actions
        missing_schema = sorted(
            paper_id
            for paper_id in paper_ids
            if schema_status.get(paper_id) == "missing"
        )
        if missing_schema:
            actions.append(
                RecommendedAction(
                    code=ACTION_MATERIALIZE_SCHEMA_RUNS,
                    message=(
                        f"materialize Workspace Schema runs for member Papers "
                        f"lacking a current run: {', '.join(missing_schema)}"
                    ),
                    target_paper_ids=missing_schema,
                )
            )
        if base_wiki.status == "missing":
            actions.append(
                RecommendedAction(
                    code=ACTION_BUILD_BASE_WIKI,
                    message="build the Workspace-owned Base Wiki for the "
                    "current membership (no snapshot exists in the Workspace "
                    "Wiki boundary)",
                )
            )
        elif base_wiki.status == "stale":
            actions.append(
                RecommendedAction(
                    code=ACTION_REBUILD_BASE_WIKI,
                    message="rebuild the Workspace-owned Base Wiki: current "
                    "authoritative inputs no longer match the recorded build "
                    "fingerprint (REQ-007/AC-010)",
                )
            )
        elif base_wiki.status == "error":
            actions.append(
                RecommendedAction(
                    code=ACTION_REPAIR_BASE_WIKI,
                    message=(
                        "repair the Workspace-owned Base Wiki: artifacts fail "
                        "integrity checks or provenance is unreadable "
                        f"({base_wiki.error_code or 'wiki_corrupt'})"
                    ),
                )
            )
        return actions


__all__ = [
    "ACTION_MATERIALIZE_SCHEMA_RUNS",
    "ACTION_BUILD_BASE_WIKI",
    "ACTION_REBUILD_BASE_WIKI",
    "ACTION_REPAIR_BASE_WIKI",
    "WorkspaceGroundingService",
]