"""Build current runtime observations through authoritative service APIs."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from sqlalchemy.orm import Session

from transit_scholar.layer3.evidence import ResearchEvidence
from transit_scholar.layer3.execution import AgentRunService
from transit_scholar.layer3.grounding import WorkspaceGroundingService
from transit_scholar.layer3.ledger import ResearchReasoningLedgerService
from transit_scholar.layer3.state import ResearchStateService

from .models import RetrievedEvidenceContext, RuntimeContextSnapshot, SessionContext
from transit_scholar.layer3.memory import EpisodicMemoryCandidate


class RuntimeContextSnapshotBuilder:
    """Read L3S1/L3S2/L3S4 state without duplicating their persistence rules."""

    def __init__(
        self,
        session: Session,
        *,
        grounding: WorkspaceGroundingService | None = None,
    ) -> None:
        self.execution = AgentRunService(session)
        self.grounding = grounding or WorkspaceGroundingService(session)
        self.state = ResearchStateService(session)
        self.ledger = ResearchReasoningLedgerService(session)

    def build(
        self,
        *,
        agent_run_id: str,
        research_session_id: str,
        retrieved_evidence: Iterable[ResearchEvidence | dict[str, Any]] = (),
        session_handoff: Any | None = None,
        episodic_memory: Iterable[EpisodicMemoryCandidate] = (),
    ) -> RuntimeContextSnapshot:
        run = self.execution.get_agent_run(agent_run_id)
        research_session = self.execution.get_research_session(
            agent_run_id, research_session_id
        )
        queries = self.ledger.list_queries(research_session_id=research_session_id)
        accepted = self.ledger.list_evidence(research_session_id=research_session_id)
        claims = self.ledger.list_claims(research_session_id=research_session_id)
        links = [
            link
            for claim in claims
            for link in self.ledger.get_claim_evidence(
                research_session_id=research_session_id, claim_id=claim.claim_id
            )
        ]
        memory = tuple(episodic_memory)
        mismatched = [item for item in memory if item.workspace_id != run.workspace_id]
        if mismatched:
            raise ValueError("episodic memory workspace does not match AgentRun")
        current_retrieval = tuple(
            self._retrieved_context(item) for item in retrieved_evidence
        )
        return RuntimeContextSnapshot(
            session=SessionContext(agent_run=run, research_session=research_session),
            workspace=self.grounding.ground(run.workspace_id),
            research_state=self.state.load_research_state(
                agent_run_id=agent_run_id, research_session_id=research_session_id
            ),
            queries=tuple(queries),
            retrieved_evidence=current_retrieval,
            accepted_evidence=tuple(accepted),
            claims=tuple(claims),
            claim_evidence_links=tuple(links),
            session_handoff=session_handoff,
            episodic_memory=memory,
        )

    @staticmethod
    def _retrieved_context(
        evidence: ResearchEvidence | dict[str, Any],
    ) -> RetrievedEvidenceContext:
        if isinstance(evidence, ResearchEvidence):
            payload = evidence.model_dump(mode="json")
            evidence_id = evidence.evidence_id
        else:
            payload = dict(evidence)
            evidence_id = str(payload.get("evidence_id", ""))
        return RetrievedEvidenceContext(evidence_id=evidence_id, payload=payload)


ContextSnapshotBuilder = RuntimeContextSnapshotBuilder

__all__ = ["ContextSnapshotBuilder", "RuntimeContextSnapshotBuilder"]
