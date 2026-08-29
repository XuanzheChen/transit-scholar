"""Stable Layer3 Stage1 error codes for Workspace Schema governance.

Extends the Workspace control-plane error vocabulary with the recommended
``schema_disabled`` and ``schema_missing`` codes (REQ-004 / AC-007).
"""

from __future__ import annotations

from transit_scholar.layer3.workspace.errors import WorkspaceError


class WorkspaceSchemaError(WorkspaceError):
    """Base error for Workspace-owned Schema operations."""

    code = "workspace_schema_error"


class SchemaDisabledError(WorkspaceSchemaError):
    """The Workspace has no Schema (schema_mode is ``none``).

    Read/materialization APIs MUST report Schema unavailable/disabled and MUST
    NOT fall back to a global or another Workspace's SchemaInstance (AC-007).
    """

    code = "schema_disabled"


class SchemaMissingError(WorkspaceSchemaError):
    """The bound Workspace has no current Workspace-owned Schema content yet.

    The Paper is a current member and the Workspace is schema-bound, but no
    Workspace-specific Schema run/current pointer exists (or the stored run
    fails integrity checks), so the content is missing rather than disabled.
    """

    code = "schema_missing"


__all__ = [
    "WorkspaceSchemaError",
    "SchemaDisabledError",
    "SchemaMissingError",
]