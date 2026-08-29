"""Layer3 Stage1 Workspace-owned Schema governance (REQ-004).

Workspace-specific Schema storage boundaries derived from the persistent
Workspace identity, with materialization/reads delegated to the existing L2S2
Package D public API through injected Workspace-specific storage roots, and
explicit disabled/missing semantics for no-schema Workspaces (AC-007).
"""

from .errors import (
    SchemaDisabledError,
    SchemaMissingError,
    WorkspaceSchemaError,
)
from .service import WorkspaceSchemaService

__all__ = [
    "WorkspaceSchemaService",
    "WorkspaceSchemaError",
    "SchemaDisabledError",
    "SchemaMissingError",
]