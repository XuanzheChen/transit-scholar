"""Normalized read-only Workspace Grounding snapshots (Layer3 Stage1, REQ-008).

``GroundedWorkspace`` is the immutable, deterministic snapshot produced by the
read-only Workspace Grounding service for upper-layer consumption. It carries
everything REQ-008/AC-012 require:

- Workspace identity, lifecycle status and revision;
- visible Paper membership with per-Paper availability of consumable assets
  (global L2S1 readiness and Workspace Schema status);
- Workspace schema mode and the immutable bound Schema identity;
- derived Workspace Schema coverage for the visible Papers;
- Base Wiki availability/freshness (the derived ``WorkspaceWikiStatus``);
- a capability summary of currently available Workspace-bound knowledge
  operations;
- optional recommended actions (REQ-008 allows Grounding to report them;
  Grounding NEVER executes them).

All statuses are derived from authoritative control-plane state and actual
underlying assets (REQ-007) — never from persisted boolean readiness flags.
The snapshot contains no timestamps beyond what the recorded Wiki provenance
carries, so identical state always grounds to an identical snapshot
(deterministic, AC-013 verification).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from transit_scholar.layer3.wiki.models import WorkspaceWikiStatus
from transit_scholar.layer3.workspace.models import (
    PaperSchemaStatus,
    SchemaBinding,
    SchemaMode,
    WorkspaceStatus,
)

#: Derived Workspace Schema coverage vocabulary (REQ-007): disabled for
#: no-schema Workspaces, empty when a bound Workspace has no member Papers,
#: and missing/partial/complete depending on how many member Papers have a
#: current Workspace-owned Schema run.
SchemaCoverageStatus = Literal["disabled", "empty", "missing", "partial", "complete"]


class GroundedPaper(BaseModel):
    """One visible Paper with its derived consumable-asset availability.

    ``l2s1_ready`` is derived by read-only inspection of the global L2S1
    canonical parse pointer and retrieval index; ``schema_status`` is derived
    from the Workspace schema mode and the Workspace-owned current Schema run
    (``disabled`` / ``missing`` / ``ready``, AC-014). Nothing here is built.

    ``schema_error_code`` carries the stable Layer3 boundary code when a
    bound Workspace's Paper Schema content is NOT ready for an explicit
    reason (``schema_missing`` for absent/corrupt/unreadable runs,
    ``schema_binding_mismatch`` for a run whose persisted Schema identity is
    incompatible with the immutable Workspace binding — REQ-004/AC-016);
    it is ``None`` for ready/disabled Papers.
    """

    workspace_id: str
    paper_id: str
    title: str | None = None
    paper_status: str = "active"
    l2s1_ready: bool = False
    schema_status: PaperSchemaStatus = "disabled"
    schema_error_code: str | None = None


class SchemaCoverage(BaseModel):
    """Derived Workspace Schema coverage for the visible Papers (REQ-008).

    ``total`` is the number of visible member Papers in every mode. For a
    no-schema Workspace the coverage is ``disabled`` and ``ready``/``missing``
    are both 0 (no run can ever exist, AC-007); for bound mode ``ready`` +
    ``missing`` == ``total`` and the status is empty/missing/partial/complete.
    """

    workspace_id: str
    total: int
    ready: int
    missing: int
    status: SchemaCoverageStatus


class WorkspaceCapabilities(BaseModel):
    """Capability summary of currently available Workspace-bound knowledge
    operations (REQ-008 / AC-012).

    Lifecycle-derived flags (``knowledge_access``, ``paper_access``,
    ``evidence_access``, ``schema_read``, ``schema_materialization``,
    ``wiki_build``, ``wiki_read``) only report what the operations would be
    *supported* for this Workspace — the per-Paper availability of actual
    content is reported by the Paper snapshots, Schema coverage and the Base
    Wiki status. ``schema_materialization`` and ``wiki_build`` are mutating
    operations whose support is reported for upper-layer planning; Grounding
    itself never executes them (AC-013).
    """

    workspace_id: str
    knowledge_access: bool
    paper_access: bool
    evidence_access: bool
    schema_read: bool
    schema_materialization: bool
    wiki_build: bool
    wiki_read: bool
    evidence_ready_papers: int
    schema_ready_papers: int


class RecommendedAction(BaseModel):
    """One Grounding-derived recommended action (reported, never executed).

    ``code`` is a stable machine-readable action identifier (e.g.
    ``materialize_schema_runs``, ``build_base_wiki``); ``target_paper_ids``
    lists the Papers the action would apply to when relevant. Grounding MUST
    NOT perform any of these actions itself (REQ-008 / C-007).
    """

    code: str
    message: str
    target_paper_ids: list[str] = Field(default_factory=list)


class GroundedWorkspace(BaseModel):
    """Immutable normalized snapshot of one Workspace (REQ-008 / AC-012).

    Every field is derived from the current authoritative control-plane state
    and actual underlying assets; the snapshot is deterministic for unchanged
    state and is safe for upper-layer consumption without further
    authorization checks at Grounding time (per-call revalidation of
    lifecycle/membership remains the responsibility of bound access objects,
    REQ-012).
    """

    workspace_id: str
    name: str
    revision: int
    status: WorkspaceStatus
    schema_mode: SchemaMode
    schema_binding: SchemaBinding | None = None
    member_paper_ids: list[str] = Field(default_factory=list)
    visible_papers: list[GroundedPaper] = Field(default_factory=list)
    schema_coverage: SchemaCoverage
    base_wiki: WorkspaceWikiStatus
    capabilities: WorkspaceCapabilities
    recommended_actions: list[RecommendedAction] = Field(default_factory=list)


__all__ = [
    "SchemaCoverageStatus",
    "GroundedPaper",
    "SchemaCoverage",
    "WorkspaceCapabilities",
    "RecommendedAction",
    "GroundedWorkspace",
]