"""Bound Workspace knowledge access gateway (Layer3 Stage1, REQ-010..012).

The gateway is the preferred runtime-facing Workspace-bound access object: it
is constructed with a ``workspace_id`` (and optionally an expected Workspace
revision) and its upper-layer public methods never take or override a
Workspace identifier (REQ-011 / AC-022). It wraps the existing lower-layer
consumable APIs — global Paper rows, L2S1 retrieval/canonical reads, and the
Layer3 Schema/Base Wiki governance services — purely as a boundary, and
enforces the Workspace access rules in code on EVERY call:

1. the Workspace must exist (``workspace_not_found``);
2. when an expected revision was bound at construction, the current
   authoritative revision must match it — otherwise ``workspace_changed`` and
   the call is rejected before any asset is read (REQ-012 / AC-023): stale
   previously-grounded state never authorizes access;
3. the Workspace must be active (``workspace_not_active``, REQ-009 / AC-016);
4. Paper-scoped operations require current membership
   (``paper_not_member``, REQ-002 / AC-015 / AC-018) BEFORE any lower-layer
   call is made — orphaned Workspace-owned files can never re-authorize a
   removed Paper.

Nothing here reimplements PDF parsing, single-Paper retrieval, Schema
extraction persistence or Wiki storage/search internals (REQ-010 / AC-024);
L2S1 delegation goes through the existing public API surface via an
injectable delegate so tests can substitute deterministic fakes.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from sqlalchemy.orm import Session

from transit_scholar.db.models import Paper

from transit_scholar.layer3.workspace.errors import (
    InvalidWorkspaceInputError,
    PaperNotFoundError,
    PaperNotMemberError,
    WorkspaceChangedError,
    WorkspaceNotActiveError,
)
from transit_scholar.layer3.workspace.models import WorkspacePaperView, WorkspaceRecord
from transit_scholar.layer3.workspace.schema_binding import SCHEMA_MODE_BOUND
from transit_scholar.layer3.workspace.service import WorkspaceService

if TYPE_CHECKING:  # pragma: no cover - typing only
    from transit_scholar.layer2.config import Layer2Config
    from transit_scholar.layer2.schema import RetrievalResult
    from transit_scholar.layer2.schema_extraction.models import (
        FieldResult,
        SchemaInstance,
    )
    from transit_scholar.layer2.wiki.models import WikiSearchResult
    from transit_scholar.layer3.schema.service import WorkspaceSchemaService
    from transit_scholar.layer3.wiki.models import WorkspaceWikiStatus
    from transit_scholar.layer3.wiki.service import WorkspaceWikiService


class L2S1EvidenceDelegate:
    """Default L2S1 evidence/canonical delegate (read-only, AC-024).

    Search/read delegate to the existing public single-paper retrieval API
    (``search_bm25`` / ``read_blocks``) with an injectable ``Layer2Config`` so
    tests can point at isolated data roots. ``l2s1_ready`` is derived by
    inspecting the global canonical parse pointer and retrieval index
    directory; nothing is ever built or mutated here (REQ-008).
    """

    def __init__(self, config: "Layer2Config | None" = None) -> None:
        if config is None:
            from transit_scholar.config import settings as global_settings
            from transit_scholar.layer2.config import Layer2Config

            config = Layer2Config.from_settings(global_settings)
        self.config = config

    def search(
        self,
        paper_id: str,
        query: str,
        *,
        top_k: int = 20,
        filters: dict[str, Any] | None = None,
    ) -> "RetrievalResult":
        from transit_scholar.layer2.retrieval.api import search_bm25  # noqa: PLC0415

        return search_bm25(
            paper_id, query, top_k=top_k, filters=filters, config=self.config
        )

    def read_blocks(self, paper_id: str, block_ids: list[str]) -> list[dict[str, Any]]:
        from transit_scholar.layer2.retrieval.api import read_blocks  # noqa: PLC0415

        return read_blocks(paper_id, block_ids, config=self.config)

    def l2s1_ready(self, paper_id: str) -> bool:
        """Derive L2S1 readiness: a current canonical parse run AND a built
        retrieval index exist for the global Paper (read-only inspection)."""
        from transit_scholar.layer2.paths import (  # noqa: PLC0415
            load_current,
            retrieval_index_dir,
        )

        current_run = load_current(self.config.parsed_paper_dir(paper_id))
        if current_run is None:
            return False
        return retrieval_index_dir(self.config, paper_id).is_dir()


class WorkspaceKnowledgeGateway:
    """Bound Workspace knowledge access (REQ-010/REQ-011/REQ-012).

    Create with the Workspace identity; optionally bind an expected
    ``revision`` captured from a Grounding snapshot so stale consumers are
    rejected with an explicit ``workspace_changed`` outcome requiring
    re-grounding (AC-023). Without an expected revision every call still
    revalidates the authoritative lifecycle and membership state, so a Paper
    removed after an earlier snapshot becomes inaccessible immediately
    (AC-015).
    """

    def __init__(
        self,
        session: Session,
        *,
        workspace_id: str,
        expected_revision: int | None = None,
        data_root: Path | str | None = None,
        workspaces: WorkspaceService | None = None,
        schemas: "WorkspaceSchemaService | None" = None,
        wiki: "WorkspaceWikiService | None" = None,
        evidence: L2S1EvidenceDelegate | None = None,
    ) -> None:
        self.session = session
        self.workspace_id = workspace_id
        self.expected_revision = expected_revision
        self.workspaces = workspaces or WorkspaceService(session)
        if schemas is None:
            from transit_scholar.layer3.schema.service import (  # noqa: PLC0415
                WorkspaceSchemaService,
            )

            schemas = WorkspaceSchemaService(session, data_root=data_root)
        if wiki is None:
            from transit_scholar.layer3.wiki.service import (  # noqa: PLC0415
                WorkspaceWikiService,
            )

            wiki = WorkspaceWikiService(session, data_root=data_root)
        self.schemas = schemas
        self.wiki = wiki
        self.evidence = evidence or L2S1EvidenceDelegate()

    # ------------------------------------------------------------------
    # state (validated on every call)
    # ------------------------------------------------------------------

    def current_state(self) -> WorkspaceRecord:
        """The current authoritative Workspace state after full revalidation."""
        return self._require_current()

    # ------------------------------------------------------------------
    # visible Papers (REQ-010; AC-014 derived readiness)
    # ------------------------------------------------------------------

    def list_papers(self) -> list[WorkspacePaperView]:
        """All visible member Papers in deterministic paper_id order."""
        self._require_current()
        memberships = self.workspaces.list_memberships(self.workspace_id)
        return [
            self._paper_view(membership.paper_id) for membership in memberships
        ]

    def get_paper(self, paper_id: str) -> WorkspacePaperView:
        """Read one visible Paper with derived availability (AC-014/AC-015)."""
        self._require_current()
        self._require_member(paper_id)
        return self._paper_view(paper_id)

    # ------------------------------------------------------------------
    # L2S1 evidence (AC-018: membership validated BEFORE delegation)
    # ------------------------------------------------------------------

    def search_evidence(
        self,
        paper_id: str,
        query: str,
        *,
        top_k: int = 20,
        filters: dict[str, Any] | None = None,
    ) -> "RetrievalResult":
        """Search one member Paper's L2S1 evidence via the public API."""
        self._require_current()
        self._require_member(paper_id)
        return self.evidence.search(paper_id, query, top_k=top_k, filters=filters)

    def read_evidence(self, paper_id: str, block_ids: list[str]) -> list[dict[str, Any]]:
        """Read canonical blocks of one member Paper via the public API."""
        if (
            not isinstance(block_ids, list)
            or not block_ids
            or not all(isinstance(block_id, str) for block_id in block_ids)
        ):
            raise InvalidWorkspaceInputError(
                "block_ids must be a non-empty list of strings"
            )
        self._require_current()
        self._require_member(paper_id)
        return self.evidence.read_blocks(paper_id, block_ids)

    # ------------------------------------------------------------------
    # Workspace-owned Schema reads (bound mode; REQ-004 / AC-020)
    # ------------------------------------------------------------------

    def get_schema_instance(
        self, paper_id: str, *, run_id: str | None = None
    ) -> "SchemaInstance":
        """Read the Workspace-owned current SchemaInstance of a member Paper."""
        self._require_current()
        self._require_member(paper_id)
        return self.schemas.get_instance(self.workspace_id, paper_id, run_id=run_id)

    def get_schema_field(
        self, paper_id: str, field_id: str, *, run_id: str | None = None
    ) -> "FieldResult":
        """Read one field's result from the Workspace-owned current run."""
        self._require_current()
        self._require_member(paper_id)
        return self.schemas.get_field(
            self.workspace_id, paper_id, field_id, run_id=run_id
        )

    # ------------------------------------------------------------------
    # Workspace-owned Base Wiki (REQ-005 / AC-008 / AC-021)
    # ------------------------------------------------------------------

    def wiki_status(self) -> "WorkspaceWikiStatus":
        """Derived Base Wiki status for the bound Workspace (read-only)."""
        self._require_current()
        return self.wiki.status(self.workspace_id)

    def search_wiki(
        self,
        query: str,
        *,
        limit: int = 20,
        mode: Literal["lexical", "semantic"] = "lexical",
    ) -> "WikiSearchResult":
        """Search the Workspace's own Base Wiki; explicit outcomes otherwise."""
        self._require_current()
        return self.wiki.search_base_only(
            self.workspace_id, query, limit=limit, mode=mode
        )

    def resolve_wiki_hit_paper_ids(self, hit: Any) -> list[str]:
        """Resolve a current Wiki search hit to member Paper identities."""
        self._require_current()
        derived = self.wiki.status(self.workspace_id)
        if derived.status != "ready":
            raise RuntimeError(
                f"workspace Wiki is not ready for discovery: {derived.status}"
            )
        wiki = self.wiki.get_wiki_service(self.workspace_id)
        if hit.type == "page":
            paper_ids = [wiki.get_page(hit.object_id).paper_id]
        elif hit.type == "entity":
            paper_ids = [
                page.paper_id for page in wiki.find_pages_by_entity(hit.object_id)
            ]
        else:
            paper_ids = []
        member_ids = {paper.paper_id for paper in self.list_papers()}
        return sorted(set(paper_ids) & member_ids)

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _require_current(self) -> WorkspaceRecord:
        """Existence + revision + active validation against the DB per call.

        Order matters: a stale expected revision is reported before lifecycle
        status, and both are validated before any membership or asset read.
        """
        record = self.workspaces.get(self.workspace_id)  # workspace_not_found
        if (
            self.expected_revision is not None
            and record.revision != self.expected_revision
        ):
            raise WorkspaceChangedError(
                f"workspace {self.workspace_id!r} changed: expected revision "
                f"{self.expected_revision}, current revision {record.revision}; "
                "re-ground against the authoritative Workspace state (AC-023)",
                expected_revision=self.expected_revision,
                current_revision=record.revision,
            )
        if record.status != "active":
            raise WorkspaceNotActiveError(
                f"workspace {self.workspace_id!r} is not active (status="
                f"{record.status!r}); normal Workspace knowledge access "
                "requires an active Workspace (AC-016)"
            )
        return record

    def _require_member(self, paper_id: str) -> None:
        member_ids = {
            membership.paper_id
            for membership in self.workspaces.list_memberships(self.workspace_id)
        }
        if paper_id not in member_ids:
            raise PaperNotMemberError(
                f"paper {paper_id!r} is not a member of workspace "
                f"{self.workspace_id!r}"
            )

    def _paper_view(self, paper_id: str) -> WorkspacePaperView:
        paper = self.session.get(Paper, paper_id)
        if paper is None:  # pragma: no cover - membership FK prevents this
            raise PaperNotFoundError(
                f"global paper {paper_id!r} does not exist"
            )
        record = self.workspaces.get(self.workspace_id)
        schema_status = "disabled"
        if record.schema_mode == SCHEMA_MODE_BOUND and record.schema_binding is not None:
            # REQ-004: readiness requires a persisted run that is readable AND
            # compatible with the immutable Workspace binding — never
            # ``current.json`` existence alone (AC-012..AC-015).
            readiness = self.schemas.paper_schema_readiness(
                self.workspace_id, [paper_id]
            ).get(paper_id)
            schema_status = (
                readiness.status if readiness is not None else "missing"
            )
        return WorkspacePaperView(
            workspace_id=self.workspace_id,
            paper_id=paper_id,
            title=paper.title,
            paper_status=paper.status,
            l2s1_ready=self.evidence.l2s1_ready(paper_id),
            schema_status=schema_status,
        )


__all__ = ["L2S1EvidenceDelegate", "WorkspaceKnowledgeGateway"]
