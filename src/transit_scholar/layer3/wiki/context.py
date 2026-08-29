"""L2S3 ``WorkspaceContext`` derivation (Layer3 Stage1, REQ-006/REQ-007).

The L2S3 ``WorkspaceContext`` is a derived value object reconstructed from the
persistent Workspace control plane — Workspace identity, immutable Schema
binding and the current Paper membership — and is NEVER used as the persistent
Layer3 Workspace domain model. Deriving it fresh on every build/status call
keeps the L2S3 layer aligned with the authoritative control plane.
"""

from __future__ import annotations

from transit_scholar.layer2.wiki.models import WorkspaceContext

from transit_scholar.layer3.workspace.models import MembershipRecord, WorkspaceRecord

from .errors import WikiEmptyMembershipError, WikiUnsupportedError


def member_paper_ids(memberships: list[MembershipRecord] | tuple[MembershipRecord, ...]) -> list[str]:
    """Deterministically ordered current member Paper ids."""
    return sorted({membership.paper_id for membership in memberships})


def derive_workspace_context(
    record: WorkspaceRecord,
    memberships: list[MembershipRecord] | tuple[MembershipRecord, ...],
) -> WorkspaceContext:
    """Derive the L2S3 ``WorkspaceContext`` for one persistent Workspace.

    Raises ``WikiUnsupportedError`` for no-schema Workspaces (REQ-005 /
    AC-009: Base Wiki construction is unsupported, never a fallback to
    another Workspace's Schema) and ``WikiEmptyMembershipError`` when the
    Workspace currently has no member Papers.
    """
    if record.schema_mode != "bound" or record.schema_binding is None:
        raise WikiUnsupportedError(
            f"workspace {record.workspace_id!r} has no bound Schema "
            f"(schema_mode={record.schema_mode!r}); Base Wiki construction is "
            "unsupported in Layer3 Stage1 (REQ-005/AC-009)"
        )
    paper_ids = member_paper_ids(memberships)
    if not paper_ids:
        raise WikiEmptyMembershipError(
            f"workspace {record.workspace_id!r} has no member Papers; a Base "
            "Wiki requires at least one member Paper"
        )
    return WorkspaceContext(
        workspace_id=record.workspace_id,
        schema_id=record.schema_binding.schema_id,
        schema_version=record.schema_binding.schema_version,
        paper_ids=paper_ids,
    )


__all__ = ["derive_workspace_context", "member_paper_ids"]