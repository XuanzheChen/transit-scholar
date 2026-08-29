"""Stable Layer3 Stage1 error/result codes (Workspace control plane).

Every failure raised by the control-plane service carries a stable ``code``
attribute matching the recommended error vocabulary so upper layers can
dispatch on machine-readable codes instead of exception types.
"""

from __future__ import annotations


class WorkspaceError(RuntimeError):
    """Base error for Layer3 Stage1 Workspace operations."""

    code: str = "workspace_error"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class WorkspaceNotFoundError(WorkspaceError):
    """No Workspace exists with the requested identifier."""

    code = "workspace_not_found"


class WorkspaceNotActiveError(WorkspaceError):
    """The Workspace exists but is not in the active lifecycle state."""

    code = "workspace_not_active"


class PaperNotFoundError(WorkspaceError):
    """The referenced global Paper does not exist in the database."""

    code = "paper_not_found"


class PaperNotMemberError(WorkspaceError):
    """The Paper is not a current member of the Workspace."""

    code = "paper_not_member"


class SchemaBindingImmutableError(WorkspaceError):
    """Schema mode/binding mutation is rejected in Layer3 Stage1 (AC-005)."""

    code = "schema_binding_immutable"


class WorkspaceChangedError(WorkspaceError):
    """The Workspace revision advanced since a consumer captured its state.

    Raised by bound access objects (REQ-012 / AC-023): a call made with a
    snapshot taken at an older Workspace revision MUST NOT authorize access;
    the consumer must re-ground against the current authoritative state. The
    error carries the expected (stale) and current revisions so callers can
    report or automatically re-ground.
    """

    code = "workspace_changed"

    def __init__(
        self,
        message: str,
        *,
        expected_revision: int | None,
        current_revision: int,
    ) -> None:
        super().__init__(message)
        self.expected_revision = expected_revision
        self.current_revision = current_revision


class InvalidWorkspaceInputError(WorkspaceError):
    """A caller-supplied argument is malformed or inconsistent."""

    code = "invalid_workspace_input"


__all__ = [
    "WorkspaceError",
    "WorkspaceNotFoundError",
    "WorkspaceNotActiveError",
    "PaperNotFoundError",
    "PaperNotMemberError",
    "SchemaBindingImmutableError",
    "WorkspaceChangedError",
    "InvalidWorkspaceInputError",
]