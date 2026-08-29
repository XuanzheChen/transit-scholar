"""Layer3 Stage1 read-only Workspace Grounding (REQ-007/REQ-008).

A normalized, side-effect-free ``GroundedWorkspace`` snapshot derived from
authoritative Workspace control-plane state (SQLAlchemy), actual Layer1/L2S1
consumable assets, Workspace-owned Schema current pointers and the
Workspace-owned Base Wiki artifacts/provenance. Grounding NEVER calls LLM or
embedding providers, never builds retrieval indexes, never extracts Schema
content, never builds/rebuilds a Wiki and never mutates any Layer1/Layer2/
Layer3 state (AC-013); recommended actions are reported in the snapshot only
and are never executed.
"""

from .models import (
    GroundedPaper,
    GroundedWorkspace,
    RecommendedAction,
    SchemaCoverage,
    SchemaCoverageStatus,
    WorkspaceCapabilities,
)
from .service import (
    ACTION_BUILD_BASE_WIKI,
    ACTION_MATERIALIZE_SCHEMA_RUNS,
    ACTION_REBUILD_BASE_WIKI,
    ACTION_REPAIR_BASE_WIKI,
    WorkspaceGroundingService,
)

__all__ = [
    "WorkspaceGroundingService",
    "GroundedWorkspace",
    "GroundedPaper",
    "SchemaCoverage",
    "SchemaCoverageStatus",
    "WorkspaceCapabilities",
    "RecommendedAction",
    "ACTION_MATERIALIZE_SCHEMA_RUNS",
    "ACTION_BUILD_BASE_WIKI",
    "ACTION_REBUILD_BASE_WIKI",
    "ACTION_REPAIR_BASE_WIKI",
]