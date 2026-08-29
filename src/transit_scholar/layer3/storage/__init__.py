"""Layer3 Stage1 Workspace-owned derived storage governance.

Workspace-specific Schema and Base Wiki storage boundaries derived from the
persistent Workspace identity (REQ-004/REQ-005/REQ-006), deterministic Base
Wiki input fingerprinting and durable build provenance (REQ-007). All heavy
persistence is delegated to the existing L2S2 ``SchemaRunStorage`` and L2S3
``WikiStore`` implementations through injected storage roots; nothing here
reimplements either persistence layer.
"""

from .fingerprint import compute_wiki_input_fingerprint, current_schema_run_identities
from .paths import (
    SCHEMAS_DIR_NAME,
    WIKI_DIR_NAME,
    WORKSPACES_DIR_NAME,
    WorkspaceStorageLayout,
    default_workspace_base_dir,
    workspace_layout,
)
from .provenance import (
    PROVENANCE_FILE,
    BuildProvenance,
    BuildProvenanceError,
    read_build_provenance,
    record_build_provenance,
)

__all__ = [
    "WorkspaceStorageLayout",
    "workspace_layout",
    "default_workspace_base_dir",
    "SCHEMAS_DIR_NAME",
    "WIKI_DIR_NAME",
    "WORKSPACES_DIR_NAME",
    "compute_wiki_input_fingerprint",
    "current_schema_run_identities",
    "PROVENANCE_FILE",
    "BuildProvenance",
    "BuildProvenanceError",
    "read_build_provenance",
    "record_build_provenance",
]