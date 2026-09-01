"""Framework-neutral Role domain contracts for Layer3 Stage5."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator

from transit_scholar.layer3.evidence import EvidenceLocator
from transit_scholar.layer3.ledger import ClaimEvidenceLink, ClaimRecord, EvidenceRecord


class RoleId(StrEnum):
    RESEARCH_COORDINATOR = "research_coordinator"
    QUERY_PLANNING = "query_planning"
    EVIDENCE_REASONING = "evidence_reasoning"
    CLAIM_REASONING = "claim_reasoning"
    FINAL_SYNTHESIS = "final_synthesis"


class RoleRuntimeProfile(BaseModel):
    """Behavioral limits owned by one Role execution."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_steps: int = Field(default=1, ge=1)
    max_llm_calls: int = Field(default=1, ge=0)
    max_tool_calls: int = Field(default=0, ge=0)
    max_failures: int = Field(default=1, ge=0)
    provider_retry_limit: int = Field(default=2, ge=0)
    structured_output_repair_limit: int = Field(default=1, ge=0)
    max_context_tokens: int | None = Field(default=None, ge=1)


class RoleInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    research_session_id: str = Field(min_length=1)


class ResearchCoordinatorInput(RoleInput):
    research_goal: str = Field(min_length=1)


class QueryPlanningInput(RoleInput):
    research_question: str = Field(min_length=1)


class EvidenceReasoningInput(RoleInput):
    evidence_ids: list[str] = Field(default_factory=list)


class ClaimReasoningInput(RoleInput):
    accepted_evidence_ids: list[str] = Field(default_factory=list)


class FinalSynthesisInput(RoleInput):
    requested_style: str | None = None
    claims: tuple[ClaimRecord, ...] = ()
    accepted_evidence: tuple[EvidenceRecord, ...] = ()
    claim_evidence_links: tuple[ClaimEvidenceLink, ...] = ()

    @model_validator(mode="after")
    def validate_durable_state_ownership(self) -> "FinalSynthesisInput":
        if any(
            item.research_session_id != self.research_session_id
            for item in (*self.claims, *self.accepted_evidence)
        ):
            raise ValueError("final synthesis state must belong to the requested session")
        claim_ids = {claim.claim_id for claim in self.claims}
        evidence_ids = {evidence.evidence_id for evidence in self.accepted_evidence}
        if any(
            link.claim_id not in claim_ids or link.evidence_id not in evidence_ids
            for link in self.claim_evidence_links
        ):
            raise ValueError("claim-evidence links must reference supplied durable state")
        return self


class RoleOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    completed: bool = False


class ResearchCoordinatorOutput(RoleOutput):
    next_role_id: RoleId | None = None
    completion_reason: str | None = None


class QueryPlanningOutput(RoleOutput):
    proposed_queries: list[str] = Field(default_factory=list)


class EvidenceReasoningOutput(RoleOutput):
    admitted_evidence_ids: list[str] = Field(default_factory=list)
    rejected_evidence_ids: list[str] = Field(default_factory=list)


class ClaimProposal(BaseModel):
    statement: str = Field(min_length=1)
    evidence_ids: list[str] = Field(default_factory=list)


class ClaimReasoningOutput(RoleOutput):
    proposed_claims: list[ClaimProposal] = Field(default_factory=list)


class FinalSourceReference(BaseModel):
    """Presentation-safe provenance copied from an admitted EvidenceRecord."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    evidence_id: str = Field(min_length=1)
    claim_ids: tuple[str, ...] = ()
    locator: EvidenceLocator
    source_metadata: dict[str, Any] = Field(default_factory=dict)
    retrieval_provenance: dict[str, Any] = Field(default_factory=dict)


class FinalResponseArtifact(RoleOutput):
    """Structured presentation boundary; natural language is confined to answer_text."""

    answer_text: str = Field(min_length=1)
    citation_references: list[str] = Field(default_factory=list)
    source_references: list[FinalSourceReference] = Field(default_factory=list)
    termination_reason: str = "semantic_completion"
    failure_message: str | None = None

    @model_validator(mode="after")
    def validate_completion_metadata(self) -> "FinalResponseArtifact":
        if len(self.citation_references) != len(set(self.citation_references)):
            raise ValueError("citation references must be unique")
        source_ids = [source.evidence_id for source in self.source_references]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("source references must be unique by evidence_id")
        if source_ids and set(source_ids) != set(self.citation_references):
            raise ValueError("source references must match citation references")
        if self.completed and self.failure_message is not None:
            raise ValueError("a completed final response cannot contain a failure message")
        return self


class FinalSynthesisOutput(FinalResponseArtifact):
    """Role output contract equivalent to the final user-facing artifact."""


class ContextPolicy(BaseModel):
    """Declarative allowlist used by a context projector."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    included_sections: frozenset[str] = Field(default_factory=frozenset)
    max_items_per_section: int | None = Field(default=None, ge=1)
    max_serialized_chars: int | None = Field(default=None, ge=2)


RoleInputT = TypeVar("RoleInputT", bound=BaseModel)
RoleOutputT = TypeVar("RoleOutputT", bound=BaseModel)


class RoleDefinition(BaseModel):
    """A predefined specialized responsibility and its strict boundaries."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True, extra="forbid")

    role_id: RoleId
    description: str = Field(min_length=1)
    prompt_template: str = Field(min_length=1)
    context_policy: ContextPolicy
    input_contract: type[BaseModel]
    output_contract: type[BaseModel]
    allowed_actions: frozenset[str] = Field(default_factory=frozenset)
    allowed_tools: frozenset[str] = Field(default_factory=frozenset)
    runtime_profile: RoleRuntimeProfile

    @model_validator(mode="after")
    def validate_contract_types(self) -> "RoleDefinition":
        if not issubclass(self.input_contract, BaseModel):
            raise ValueError("input_contract must be a Pydantic model")
        if not issubclass(self.output_contract, BaseModel):
            raise ValueError("output_contract must be a Pydantic model")
        return self

    @field_serializer("input_contract", "output_contract")
    def serialize_contract(self, contract: type[BaseModel]) -> str:
        return f"{contract.__module__}.{contract.__qualname__}"


class RuntimeUsage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    llm_calls: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    failures: int = Field(default=0, ge=0)


class RetryState(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider_retries: int = Field(default=0, ge=0)
    structured_output_repairs: int = Field(default=0, ge=0)


class RoleWorkingState(BaseModel):
    """Temporary, recoverable Role state distinct from the research ledger."""

    model_config = ConfigDict(extra="forbid")
    current_step: int = Field(default=0, ge=0)
    last_observation: dict[str, Any] | None = None
    last_output: dict[str, Any] | None = None
    intermediate_artifacts: list[dict[str, Any]] = Field(default_factory=list)
    next_action_index: int = Field(default=0, ge=0)
    operation_in_flight: dict[str, Any] | None = None
    usage: RuntimeUsage = Field(default_factory=RuntimeUsage)
    retries: RetryState = Field(default_factory=RetryState)


RoleExecutionStatus = str


class RoleExecution(BaseModel):
    """Durable snapshot sufficient to inspect or recover Role execution."""

    model_config = ConfigDict(extra="forbid")
    role_execution_id: str = Field(min_length=1)
    role_id: RoleId
    agent_run_id: str = Field(min_length=1)
    research_session_id: str = Field(min_length=1)
    trace_scope: str = Field(min_length=1)
    status: RoleExecutionStatus = "created"
    working_state: RoleWorkingState = Field(default_factory=RoleWorkingState)
    runtime_profile: RoleRuntimeProfile
    termination_reason: str | None = None
    failure_message: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None

    def start(self) -> None:
        self.status = "running"
        self.started_at = self.started_at or datetime.now(timezone.utc)

    def end(self, *, status: str, reason: str, failure_message: str | None = None) -> None:
        self.status = status
        self.termination_reason = reason
        self.failure_message = failure_message
        self.ended_at = datetime.now(timezone.utc)


class RoleResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role_execution_id: str = Field(min_length=1)
    role_id: RoleId
    status: str
    output: dict[str, Any] | None = None
    working_state: RoleWorkingState
    termination_reason: str
    failure_message: str | None = None


class RolePolicy(Protocol):
    def decide(
        self, definition: RoleDefinition, role_input: BaseModel, state: RoleWorkingState
    ) -> BaseModel: ...


__all__ = [name for name in globals() if not name.startswith("_")]
