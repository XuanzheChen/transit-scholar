"""Immutable structured context contracts for Layer3 Stage5."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from transit_scholar.layer3.execution import AgentRunRecord, ResearchSessionRecord
from transit_scholar.layer3.grounding import GroundedWorkspace
from transit_scholar.layer3.ledger import (
    ClaimEvidenceLink,
    ClaimRecord,
    EvidenceRecord,
    ResearchQueryRecord,
)
from transit_scholar.layer3.state import ResearchStateRecord


CONTEXT_SECTIONS = frozenset(
    {
        "session",
        "workspace",
        "research_state",
        "queries",
        "retrieved_evidence",
        "accepted_evidence",
        "claims",
        "claim_evidence_links",
    }
)


class SessionContext(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    agent_run: AgentRunRecord
    research_session: ResearchSessionRecord


class RetrievedEvidenceContext(BaseModel):
    """Structured current L3S3 observation, not long-term memory."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    evidence_id: str = Field(min_length=1)
    payload: dict[str, Any]


class RuntimeContextSnapshot(BaseModel):
    """Authoritative observation assembled from L3S1-L3S4 boundaries."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    session: SessionContext
    workspace: GroundedWorkspace
    research_state: ResearchStateRecord | None = None
    queries: tuple[ResearchQueryRecord, ...] = ()
    retrieved_evidence: tuple[RetrievedEvidenceContext, ...] = ()
    accepted_evidence: tuple[EvidenceRecord, ...] = ()
    claims: tuple[ClaimRecord, ...] = ()
    claim_evidence_links: tuple[ClaimEvidenceLink, ...] = ()


class RoleContext(BaseModel):
    """The complete and exclusive structured input context visible to a Role."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    role_id: str = Field(min_length=1)
    sections: dict[str, Any]
    omitted_sections: frozenset[str]
    serialized_chars: int = Field(ge=2)
    truncated: bool = False

    def require(self, section: str) -> Any:
        """Return an allowed section; omitted data has no fallback path."""
        return self.sections[section]


__all__ = [
    "CONTEXT_SECTIONS",
    "RetrievedEvidenceContext",
    "RoleContext",
    "RuntimeContextSnapshot",
    "SessionContext",
]
