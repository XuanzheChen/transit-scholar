"""Stable Layer3 Stage1 error codes for Workspace Base Wiki governance.

Extends the Workspace control-plane error vocabulary with the recommended
``wiki_unsupported`` / ``wiki_missing`` / ``wiki_stale`` codes plus a generic
Workspace Wiki error (REQ-005 / REQ-007 / AC-008..11).
"""

from __future__ import annotations

from transit_scholar.layer3.workspace.errors import WorkspaceError


class WorkspaceWikiError(WorkspaceError):
    """Base error for Workspace-owned Base Wiki operations."""

    code = "workspace_wiki_error"


class WikiUnsupportedError(WorkspaceWikiError):
    """Base Wiki construction is unsupported for this Workspace.

    In Layer3 Stage1 a no-schema Workspace has no Base Wiki build capability;
    the system MUST NOT silently construct a Wiki from another Schema or
    another Workspace (REQ-005 / AC-009).
    """

    code = "wiki_unsupported"


class WikiMissingError(WorkspaceWikiError):
    """The Workspace has no Base Wiki snapshot in its own storage boundary.

    A Workspace A Wiki snapshot is never returned when Workspace B requests
    its Base Wiki (AC-008); the answer is an explicit missing result.
    """

    code = "wiki_missing"


class WikiStaleError(WorkspaceWikiError):
    """The Workspace's Base Wiki inputs changed since the last build.

    Derived from the deterministic input fingerprint comparison (REQ-007 /
    AC-010): the Wiki artifacts still exist but are observably non-current, so
    read paths return this explicit degraded/stale outcome.
    """

    code = "wiki_stale"


class WikiCorruptError(WorkspaceWikiError):
    """The Workspace's Base Wiki artifacts fail existing integrity checks."""

    code = "wiki_corrupt"


class WikiEmptyMembershipError(WorkspaceWikiError):
    """The schema-bound Workspace currently has no member Papers.

    A Base Wiki requires at least one member Paper; an empty membership is an
    explicit invalid state for build construction, never a silent empty Wiki.
    """

    code = "empty_membership"


__all__ = [
    "WorkspaceWikiError",
    "WikiUnsupportedError",
    "WikiMissingError",
    "WikiStaleError",
    "WikiCorruptError",
    "WikiEmptyMembershipError",
]