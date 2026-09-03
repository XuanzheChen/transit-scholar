"""Normalized Workspace Base Wiki status snapshots (Layer3 Stage1).

Read-only, derived results for Grounding-style consumers (REQ-007). The
status is computed from the authoritative control plane (Workspace schema
mode, current membership, Schema binding), the file-backed Wiki artifacts and
the recorded build input fingerprint — never from a persisted boolean
readiness flag.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from transit_scholar.layer2.wiki.application import (
    WorkspaceWikiApplicationBuildResult,
)
from transit_scholar.layer3.storage.provenance import BuildProvenance

#: Derived Base Wiki status vocabulary (REQ-007).
WikiDerivedStatus = Literal["unsupported", "missing", "ready", "stale", "error"]


class WorkspaceWikiStatus(BaseModel):
    """Derived Base Wiki status for one Workspace (read-only derivation).

    - ``unsupported`` — no-schema Workspace: Base Wiki capability is
      unsupported in Layer3 Stage1 (REQ-005 / AC-009);
    - ``missing`` — schema-bound but no snapshot exists in the Workspace's own
      Wiki boundary (AC-008);
    - ``ready`` — the recorded input fingerprint matches the current inputs,
      the recorded provenance and the persisted Manifest build are both
      ``complete``, the artifacts pass integrity checks, and a current,
      compatible mandatory persistent vector index with complete Page/Entity
      coverage exists (REQ-001 / AC-006);
    - ``stale`` — artifacts exist but the current authoritative inputs no
      longer match the recorded fingerprint (AC-010); when the mismatch comes
      from a member Paper's current Workspace Schema run failing binding or
      readability validation, the derived ``error_code`` is
      ``schema_input_invalid`` (REQ-002 / AC-005..AC-007), otherwise
      ``input_fingerprint_mismatch`` for a genuine input change;
    - ``error`` — artifacts fail integrity checks, provenance is corrupt or
      non-complete, the Manifest build is partial/failed, or the mandatory
      vector index is missing/stale/incompatible (AC-001..AC-005).
    """

    workspace_id: str
    status: WikiDerivedStatus
    manifest_status: str | None = None
    fingerprint: str | None = None
    recorded_fingerprint: str | None = None
    build_revision: int | None = None
    built_at: datetime | None = None
    error_code: str | None = None


class WorkspaceWikiBuildOutcome(BaseModel):
    """Outcome of one Workspace Base Wiki build, including provenance."""

    result: WorkspaceWikiApplicationBuildResult
    fingerprint: str
    provenance: BuildProvenance


class WorkspaceWikiCapability(BaseModel):
    """Capability summary of Base Wiki operations for one Workspace."""

    workspace_id: str
    build_supported: bool
    read_supported: bool
    status: WikiDerivedStatus
    reason: str | None = Field(default=None)


class WorkspaceWikiHit(BaseModel):
    """Unified hit retaining the semantic source of Wiki knowledge."""
    type: Literal["page", "entity", "entry"]
    object_id: str
    title: str
    score: float = 0.0
    snippet: str
    retrieval_mode: Literal["lexical", "semantic"] = "lexical"
    source_kind: Literal["base_wiki", "agentic_wiki"]
    lifecycle_status: Literal["active", "stale"] | None = None
    source_score: float | None = None
    local_rank: int | None = None
    fusion_score: float | None = None


class WorkspaceWikiSearchResult(BaseModel):
    status: Literal["ok", "degraded", "error"] = "ok"
    hits: list[WorkspaceWikiHit] = Field(default_factory=list)
    error_code: str | None = None
    source_status: dict[str, str] = Field(default_factory=dict)
    source_errors: dict[str, str | None] = Field(default_factory=dict)


__all__ = [
    "WikiDerivedStatus",
    "WorkspaceWikiStatus",
    "WorkspaceWikiBuildOutcome",
    "WorkspaceWikiCapability",
    "WorkspaceWikiHit",
    "WorkspaceWikiSearchResult",
]
