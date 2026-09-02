"""Serializable run-level state, outcomes, and bounded handoff contracts."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

SessionOutcomeStatus = Literal["completed", "failed", "cancelled", "terminated"]
RunStatus = Literal["created", "running", "completed", "failed", "cancelled", "terminated"]


class SessionOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    research_session_id: str = Field(min_length=1)
    research_question: str = Field(min_length=1)
    status: SessionOutcomeStatus
    key_claims: list[Any] = Field(default_factory=list)
    claim_refs: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)
    source_provenance: list[dict[str, Any]] = Field(default_factory=list)
    final_response: str | None = None
    final_summary: str | None = None
    failure_reason: str | None = None
    termination_metadata: dict[str, Any] = Field(default_factory=dict)


class RunOrchestrationState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_run_id: str = Field(min_length=1)
    research_plan_id: str | None = None
    current_plan_item_id: str | None = None
    current_research_session_id: str | None = None
    completed_session_ids: list[str] = Field(default_factory=list)
    failed_session_ids: list[str] = Field(default_factory=list)
    planning_round: int = Field(default=0, ge=0)
    run_steps: int = Field(default=0, ge=0)
    usage: dict[str, int] = Field(default_factory=dict)
    status: RunStatus = "created"
    termination_reason: str | None = None


class RunRuntimeConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    max_sessions: int = Field(default=10, ge=1)
    max_planning_rounds: int = Field(default=10, ge=1)
    max_failed_sessions: int = Field(default=3, ge=0)
    max_run_steps: int = Field(default=100, ge=1)
    max_prior_sessions: int = Field(default=5, ge=0)
    max_claims_per_session: int = Field(default=20, ge=0)
    max_handoff_items: int = Field(default=100, ge=0)
    max_serialized_chars: int = Field(default=12000, ge=1)
    max_coordination_claims: int = Field(default=100, ge=0)
    max_coordination_claim_refs: int = Field(default=100, ge=0)
    max_coordination_unresolved_items: int = Field(default=50, ge=0)
    max_coordination_conflicting_items: int = Field(default=50, ge=0)
    max_coordination_plan_items: int = Field(default=50, ge=0)


class RunContextSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_run_id: str = Field(min_length=1)
    user_goal: str = Field(min_length=1)
    research_plan: Any | None = None
    session_outcomes: list[SessionOutcome] = Field(default_factory=list)
    active_session_ids: list[str] = Field(default_factory=list)
    failed_session_ids: list[str] = Field(default_factory=list)
    claim_refs: list[str] = Field(default_factory=list)
    unresolved_items: list[str] = Field(default_factory=list)
    conflicting_items: list[str] = Field(default_factory=list)
    orchestration_state: RunOrchestrationState | None = None


class RunCoordinatorSessionSummary(BaseModel):
    """Research-result view of one prior Session for run-level planning."""

    model_config = ConfigDict(extra="forbid")

    research_session_id: str = Field(min_length=1)
    research_question: str = Field(min_length=1)
    status: SessionOutcomeStatus
    final_summary: str | None = None
    failure_reason: str | None = None
    claim_refs: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)


class RunCoordinatorContext(BaseModel):
    """Bounded, run-level semantic context supplied to the coordinator."""

    model_config = ConfigDict(extra="forbid")

    agent_run_id: str = Field(min_length=1)
    user_goal: str = Field(min_length=1)
    research_plan: dict[str, Any] | None = None
    prior_sessions: list[RunCoordinatorSessionSummary] = Field(default_factory=list)
    key_claims: list[str] = Field(default_factory=list)
    claim_refs: list[str] = Field(default_factory=list)
    unresolved_items: list[str] = Field(default_factory=list)
    conflicting_items: list[str] = Field(default_factory=list)
    active_session_ids: list[str] = Field(default_factory=list)
    failed_session_ids: list[str] = Field(default_factory=list)
    orchestration_state: dict[str, Any] | None = None

    @property
    def prior_session_summaries(self) -> list[RunCoordinatorSessionSummary]:
        """Compatibility/readability alias for the bounded Session view."""
        return self.prior_sessions

    @property
    def completed_sessions(self) -> list[RunCoordinatorSessionSummary]:
        return [session for session in self.prior_sessions if session.status == "completed"]

    @property
    def failed_sessions(self) -> list[RunCoordinatorSessionSummary]:
        return [session for session in self.prior_sessions if session.status != "completed"]


class SessionHandoffContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_goal: str = Field(min_length=1)
    current_research_question: str = Field(min_length=1)
    prior_session_summaries: list[str] = Field(default_factory=list)
    relevant_prior_claims: list[Any] = Field(default_factory=list)
    unresolved_or_conflicting_items: list[str] = Field(default_factory=list)
    provenance_refs: list[str] = Field(default_factory=list)


class RunFinalResponseArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer_text: str
    citation_refs: list[str] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)
    contributing_session_ids: list[str] = Field(default_factory=list)
    status: Literal["completed", "failed", "cancelled", "terminated"] = "completed"
    completion_reason: str | None = None
    failure_metadata: dict[str, Any] = Field(default_factory=dict)
