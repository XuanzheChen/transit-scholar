"""Layer3 Stage5 context governance public API."""

from .builder import ContextSnapshotBuilder, RuntimeContextSnapshotBuilder
from .models import (
    CONTEXT_SECTIONS,
    RetrievedEvidenceContext,
    RoleContext,
    RuntimeContextSnapshot,
    SessionContext,
)
from .projector import (
    ContextBudgetExceededError,
    InvalidContextPolicyError,
    RoleContextProjector,
)

__all__ = [
    "CONTEXT_SECTIONS",
    "ContextSnapshotBuilder",
    "ContextBudgetExceededError",
    "InvalidContextPolicyError",
    "RetrievedEvidenceContext",
    "RoleContext",
    "RoleContextProjector",
    "RuntimeContextSnapshot",
    "RuntimeContextSnapshotBuilder",
    "SessionContext",
]
