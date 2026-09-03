"""Non-owning access facade over authoritative current-run Layer3 state."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from transit_scholar.layer3.agent.models import RoleWorkingState
from transit_scholar.layer3.context import RuntimeContextSnapshot
from transit_scholar.layer3.execution import AgentRunRecord, ResearchSessionRecord
from transit_scholar.layer3.ledger import ClaimRecord, EvidenceRecord, ResearchQueryRecord
from transit_scholar.layer3.run_context import (
    RunContextSnapshot,
    RunOrchestrationState,
    SessionHandoffContext,
)
from transit_scholar.layer3.state import ResearchStateRecord


class WorkingMemoryBoundaryError(ValueError):
    """Raised when current-run sources cross a run or Workspace boundary."""


@dataclass(frozen=True, slots=True)
class WorkingMemory:
    """A read-through view whose values remain owned by L3S2-L3S6 components.

    The facade has no save/update API and deliberately defines no serializable
    long-term Working Memory record. Objects returned by its accessors are the
    same objects supplied by the authoritative components.
    """

    workspace_id: str
    agent_run_id: str
    agent_run: AgentRunRecord
    research_sessions: Sequence[ResearchSessionRecord] = ()
    research_states: Mapping[str, ResearchStateRecord] = field(default_factory=dict)
    queries: Sequence[ResearchQueryRecord] = ()
    evidence: Sequence[EvidenceRecord] = ()
    claims: Sequence[ClaimRecord] = ()
    role_working_states: Mapping[str, RoleWorkingState] = field(default_factory=dict)
    run_orchestration_state: RunOrchestrationState | None = None
    runtime_context_snapshots: Mapping[str, RuntimeContextSnapshot] = field(default_factory=dict)
    run_context_snapshot: RunContextSnapshot | None = None
    session_handoff_contexts: Mapping[str, SessionHandoffContext] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.workspace_id or not self.agent_run_id:
            raise WorkingMemoryBoundaryError("workspace_id and agent_run_id are required")
        if self.agent_run.workspace_id != self.workspace_id:
            raise WorkingMemoryBoundaryError("AgentRun belongs to another Workspace")
        if self.agent_run.agent_run_id != self.agent_run_id:
            raise WorkingMemoryBoundaryError("AgentRun does not match the facade run")
        for session in self.research_sessions:
            if session.agent_run_id != self.agent_run_id:
                raise WorkingMemoryBoundaryError("ResearchSession belongs to another AgentRun")
        if self.run_orchestration_state is not None:
            if self.run_orchestration_state.agent_run_id != self.agent_run_id:
                raise WorkingMemoryBoundaryError("orchestration state belongs to another AgentRun")
        if self.run_context_snapshot is not None:
            if self.run_context_snapshot.agent_run_id != self.agent_run_id:
                raise WorkingMemoryBoundaryError("run context belongs to another AgentRun")
        for snapshot in self.runtime_context_snapshots.values():
            if snapshot.session.agent_run.workspace_id != self.workspace_id:
                raise WorkingMemoryBoundaryError("runtime context belongs to another Workspace")
            if snapshot.session.agent_run.agent_run_id != self.agent_run_id:
                raise WorkingMemoryBoundaryError("runtime context belongs to another AgentRun")

    @property
    def session_ids(self) -> tuple[str, ...]:
        return tuple(session.research_session_id for session in self.research_sessions)

    def research_state_for(self, research_session_id: str) -> ResearchStateRecord | None:
        return self.research_states.get(research_session_id)

    def runtime_context_for(self, research_session_id: str) -> RuntimeContextSnapshot | None:
        return self.runtime_context_snapshots.get(research_session_id)

    def handoff_for(self, research_session_id: str) -> SessionHandoffContext | None:
        return self.session_handoff_contexts.get(research_session_id)

    def role_state_for(self, role_execution_id: str) -> RoleWorkingState | None:
        return self.role_working_states.get(role_execution_id)

    def sources(self) -> Mapping[str, Any]:
        """Expose named references without creating a mutable aggregate store."""
        return MappingProxyType(
            {
                "agent_run": self.agent_run,
                "research_sessions": self.research_sessions,
                "research_states": self.research_states,
                "queries": self.queries,
                "evidence": self.evidence,
                "claims": self.claims,
                "role_working_states": self.role_working_states,
                "run_orchestration_state": self.run_orchestration_state,
                "runtime_context_snapshots": self.runtime_context_snapshots,
                "run_context_snapshot": self.run_context_snapshot,
                "session_handoff_contexts": self.session_handoff_contexts,
            }
        )


WorkingMemoryFacade = WorkingMemory

__all__ = ["WorkingMemory", "WorkingMemoryBoundaryError", "WorkingMemoryFacade"]
