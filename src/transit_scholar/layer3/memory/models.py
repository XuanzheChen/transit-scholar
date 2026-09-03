"""Domain contracts for the three distinct Layer3 memory semantics."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class MemoryKind(StrEnum):
    """The semantic ownership categories used by L3S7."""

    WORKING = "working_memory"
    EPISODIC = "episodic_memory"
    SEMANTIC = "semantic_memory"


class MemorySourceKind(StrEnum):
    """Explicit identities for memory and neighboring knowledge sources."""

    WORKING_MEMORY = "working_memory"
    EPISODIC_MEMORY = "episodic_memory"
    BASE_WIKI = "base_wiki"
    AGENTIC_WIKI = "agentic_wiki"
    EVIDENCE = "evidence"
    CLAIM = "claim"
    RAG = "rag"
    SCHEMA = "schema"
    SESSION_HANDOFF = "session_handoff"


class EpisodicMemoryProvenance(BaseModel):
    """Durable identities connecting an episode to its authoritative run state."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    workspace_id: str = Field(min_length=1)
    agent_run_id: str = Field(min_length=1)
    research_session_ids: tuple[str, ...] = ()
    claim_ids: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    durable_state_refs: tuple[str, ...] = ()


class EpisodicMemoryRecord(BaseModel):
    """One Workspace-scoped record of experience from one complete AgentRun."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    memory_id: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    agent_run_id: str = Field(min_length=1)
    user_goal_raw: str = Field(min_length=1)
    goal_summary: str = Field(min_length=1)
    research_summary: str = Field(min_length=1)
    important_claim_ids: tuple[str, ...] = ()
    useful_queries: tuple[str, ...] = ()
    failed_or_unhelpful_queries: tuple[str, ...] = ()
    unresolved_summary: str
    final_outcome: str = Field(min_length=1)
    provenance: EpisodicMemoryProvenance
    created_at: datetime
    source_kind: MemorySourceKind = MemorySourceKind.EPISODIC_MEMORY
    is_authoritative_evidence: bool = False

    @model_validator(mode="after")
    def validate_identity_and_semantics(self) -> "EpisodicMemoryRecord":
        if self.provenance.workspace_id != self.workspace_id:
            raise ValueError("provenance workspace_id must match the episode")
        if self.provenance.agent_run_id != self.agent_run_id:
            raise ValueError("provenance agent_run_id must match the episode")
        if not set(self.important_claim_ids).issubset(self.provenance.claim_ids):
            raise ValueError("important claims must be present in provenance")
        if self.source_kind is not MemorySourceKind.EPISODIC_MEMORY:
            raise ValueError("an episode must use the episodic_memory source kind")
        if self.is_authoritative_evidence:
            raise ValueError("episodic memory is auxiliary, not evidence")
        return self

    @staticmethod
    def canonical_memory_id(agent_run_id: str) -> str:
        """Return the sole stable V1 episode identity for an AgentRun."""
        if not agent_run_id:
            raise ValueError("agent_run_id must not be empty")
        return f"episodic-memory:{agent_run_id}"

    @property
    def canonical_episode_key(self) -> tuple[str, str]:
        """The uniqueness boundary used by an episodic repository."""
        return self.workspace_id, self.agent_run_id


__all__ = [
    "EpisodicMemoryProvenance",
    "EpisodicMemoryRecord",
    "MemoryKind",
    "MemorySourceKind",
]
