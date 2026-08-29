"""Layer3 Stage1 Workspace-owned Schema governance (REQ-004).

Workspace-specific Schema storage boundaries derived from the persistent
Workspace identity, with materialization/reads delegated to the existing L2S2
Package D public API through injected Workspace-specific storage roots, and
explicit disabled/missing semantics for no-schema Workspaces (AC-007).
"""

from .errors import (
    SchemaBindingMismatchError,
    SchemaDisabledError,
    SchemaMissingError,
    WorkspaceSchemaError,
)
from .service import PaperSchemaReadiness, WorkspaceSchemaService

__all__ = [
    "WorkspaceSchemaService",
    "PaperSchemaReadiness",
    "WorkspaceSchemaError",
    "SchemaDisabledError",
    "SchemaMissingError",
    "SchemaBindingMismatchError",
]