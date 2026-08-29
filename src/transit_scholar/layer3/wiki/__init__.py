"""Layer3 Stage1 Workspace-owned Base Wiki governance (REQ-005/REQ-007).

Workspace-specific Base Wiki storage boundaries, L2S3 composition reuse through
injected storage roots, derived freshness from the deterministic input
fingerprint (AC-010/AC-011), and explicit unsupported semantics for no-schema
Workspaces (AC-009).
"""

from .context import derive_workspace_context, member_paper_ids
from .errors import (
    WikiCorruptError,
    WikiEmptyMembershipError,
    WikiMissingError,
    WikiStaleError,
    WikiUnsupportedError,
    WorkspaceWikiError,
)
from .models import (
    WikiDerivedStatus,
    WorkspaceWikiBuildOutcome,
    WorkspaceWikiCapability,
    WorkspaceWikiStatus,
)
from .service import WorkspaceWikiService

__all__ = [
    "WorkspaceWikiService",
    "derive_workspace_context",
    "member_paper_ids",
    "WorkspaceWikiError",
    "WikiUnsupportedError",
    "WikiMissingError",
    "WikiStaleError",
    "WikiCorruptError",
    "WikiEmptyMembershipError",
    "WikiDerivedStatus",
    "WorkspaceWikiStatus",
    "WorkspaceWikiBuildOutcome",
    "WorkspaceWikiCapability",
]
