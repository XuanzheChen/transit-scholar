"""Layer3 Stage1 Workspace-owned Schema governance (REQ-004).

Workspace-specific Schema storage boundaries derived from the persistent
Workspace identity, with materialization/reads delegated to the existing L2S2
Package D public API through injected Workspace-specific storage roots, and
explicit disabled/missing semantics for no-schema Workspaces (AC-007).

The governed current-run build snapshot (``ValidatedCurrentSchemaRun``,
captured by ``WorkspaceSchemaService.capture_current_run`` /
``capture_current_runs``) returns the exact ``SchemaInstance`` and the exact
run identity from ONE validated current persisted run (T-001 / REQ-001 /
AC-001..AC-002).
"""

from .errors import (
    SchemaBindingMismatchError,
    SchemaDisabledError,
    SchemaMissingError,
    WorkspaceSchemaError,
)
from .service import PaperSchemaReadiness, WorkspaceSchemaService
from .snapshot import ValidatedCurrentSchemaRun

__all__ = [
    "WorkspaceSchemaService",
    "PaperSchemaReadiness",
    "ValidatedCurrentSchemaRun",
    "WorkspaceSchemaError",
    "SchemaDisabledError",
    "SchemaMissingError",
    "SchemaBindingMismatchError",
]