"""Layer3 Stage1 Workspace control plane.

Persistent Workspace domain model, immutable Schema binding semantics,
Workspace-to-Paper membership, lifecycle governance (archive/delete), and the
bound Workspace knowledge access gateway. The control plane lives in the
project database layer (``transit_scholar.db.models.Workspace`` /
``WorkspacePaperMembership``); this package provides the authoritative
service boundary, normalized snapshots, and code-enforced Workspace access.

The gateway implementation lives in the dedicated ``layer3.knowledge``
package; for backward compatibility this package re-exports it lazily (PEP
562) through ``__getattr__`` so ``from transit_scholar.layer3.workspace import
WorkspaceKnowledgeGateway`` keeps working without a module-init cycle.
"""

from __future__ import annotations

from .errors import (
    InvalidWorkspaceInputError,
    PaperNotFoundError,
    PaperNotMemberError,
    SchemaBindingImmutableError,
    WorkspaceChangedError,
    WorkspaceError,
    WorkspaceNotActiveError,
    WorkspaceNotFoundError,
)
from .models import (
    AddPaperResult,
    ArchiveWorkspaceResult,
    CreateWorkspaceResult,
    DeleteWorkspaceResult,
    MembershipRecord,
    PaperSchemaStatus,
    RemovePaperResult,
    SchemaBinding,
    WorkspacePaperView,
    WorkspaceRecord,
)
from .schema_binding import (
    SCHEMA_MODE_BOUND,
    SCHEMA_MODE_NONE,
    binding_for,
    compute_schema_hash,
)
from .service import WorkspaceService

#: Names re-exported from the ``layer3.knowledge`` gateway package.
_GATEWAY_EXPORTS = ("WorkspaceKnowledgeGateway", "L2S1EvidenceDelegate")


def __getattr__(name: str):
    """Lazily re-export the Workspace knowledge gateway (PEP 562).

    The gateway lives in ``transit_scholar.layer3.knowledge``; importing it
    eagerly here would create a module-init cycle because the gateway itself
    imports the Workspace control plane. Lazy resolution keeps both import
    orders (``layer3.knowledge`` first or ``layer3.workspace`` first) safe.
    """
    if name in _GATEWAY_EXPORTS:
        from transit_scholar.layer3.knowledge.gateway import (  # noqa: PLC0415
            L2S1EvidenceDelegate,
            WorkspaceKnowledgeGateway,
        )

        return {
            "WorkspaceKnowledgeGateway": WorkspaceKnowledgeGateway,
            "L2S1EvidenceDelegate": L2S1EvidenceDelegate,
        }[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "WorkspaceService",
    "WorkspaceError",
    "WorkspaceNotFoundError",
    "WorkspaceNotActiveError",
    "PaperNotFoundError",
    "PaperNotMemberError",
    "SchemaBindingImmutableError",
    "WorkspaceChangedError",
    "InvalidWorkspaceInputError",
    "SchemaBinding",
    "WorkspaceRecord",
    "MembershipRecord",
    "CreateWorkspaceResult",
    "AddPaperResult",
    "RemovePaperResult",
    "ArchiveWorkspaceResult",
    "DeleteWorkspaceResult",
    "WorkspacePaperView",
    "PaperSchemaStatus",
    "WorkspaceKnowledgeGateway",
    "L2S1EvidenceDelegate",
    "SCHEMA_MODE_BOUND",
    "SCHEMA_MODE_NONE",
    "compute_schema_hash",
    "binding_for",
]