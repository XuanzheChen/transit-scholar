"""Claim-Evidence many-to-many relationship and ownership tests."""

from __future__ import annotations

import uuid

import pytest

from transit_scholar.layer3.evidence import EvidenceLocator, ResearchEvidence
from transit_scholar.layer3.execution import AgentRunService
from transit_scholar.layer3.ledger import (
    ClaimNotFoundError,
    EvidenceNotFoundError,
    EvidenceOwnershipError,
    InvalidClaimEvidenceRelationError,
    ResearchQueryLedgerService,
)
from transit_scholar.layer3.workspace import WorkspaceService


def _session_id(session) -> str:
    workspace = WorkspaceService(session).create(
        name=f"Claim Evidence Workspace {uuid.uuid4().hex}"
    ).workspace
    run = AgentRunService(session).create_agent_run(
        workspace_id=workspace.workspace_id, user_goal="Claim evidence test"
    )
    return AgentRunService(session).create_research_session(
        agent_run_id=run.agent_run_id, research_question="Test question"
    ).research_session_id


def _evidence(evidence_id: str) -> ResearchEvidence:
    return ResearchEvidence(
        evidence_id=evidence_id,
        locator=EvidenceLocator(workspace_id="workspace", source_kind="paper", paper_id="paper"),
        text=f"Evidence {evidence_id}",
        source_kind="paper",
    )


def _admit(ledger, research_session_id: str, evidence_id: str):
    query = ledger.create_query(
        research_session_id=research_session_id, query_text=f"Query {evidence_id}"
    )
    return ledger.admit_evidence(
        research_session_id=research_session_id,
        source_query_id=query.query_id,
        evidence=_evidence(evidence_id),
    )


def test_claim_evidence_supports_contradictions_many_to_many_and_unlink(session):
    research_session_id = _session_id(session)
    ledger = ResearchQueryLedgerService(session)
    supporting = _admit(ledger, research_session_id, "supporting")
    contradicting = _admit(ledger, research_session_id, "contradicting")
    first_claim = ledger.create_claim(research_session_id=research_session_id, statement="Claim one")
    second_claim = ledger.create_claim(research_session_id=research_session_id, statement="Claim two")

    ledger.link_evidence_to_claim(
        research_session_id=research_session_id,
        claim_id=first_claim.claim_id,
        evidence_id=supporting.evidence_id,
        relation="supports",
    )
    ledger.link_evidence_to_claim(
        research_session_id=research_session_id,
        claim_id=first_claim.claim_id,
        evidence_id=contradicting.evidence_id,
        relation="contradicts",
    )
    ledger.link_evidence_to_claim(
        research_session_id=research_session_id,
        claim_id=second_claim.claim_id,
        evidence_id=supporting.evidence_id,
        relation="supports",
    )

    links = ledger.get_claim_evidence(
        research_session_id=research_session_id, claim_id=first_claim.claim_id
    )
    assert {(link.evidence_id, link.relation) for link in links} == {
        (supporting.evidence_id, "supports"),
        (contradicting.evidence_id, "contradicts"),
    }
    ledger.unlink_evidence_from_claim(
        research_session_id=research_session_id,
        claim_id=first_claim.claim_id,
        evidence_id=supporting.evidence_id,
    )
    remaining = ledger.get_claim_evidence(
        research_session_id=research_session_id, claim_id=first_claim.claim_id
    )
    assert [(link.evidence_id, link.relation) for link in remaining] == [
        (contradicting.evidence_id, "contradicts")
    ]


def test_claim_evidence_rejects_cross_session_missing_and_invalid_references(session):
    first_session_id = _session_id(session)
    second_session_id = _session_id(session)
    ledger = ResearchQueryLedgerService(session)
    claim = ledger.create_claim(research_session_id=first_session_id, statement="Claim")
    local_evidence = _admit(ledger, first_session_id, "local-evidence")
    evidence = _admit(ledger, second_session_id, "other-session-evidence")

    with pytest.raises(EvidenceOwnershipError):
        ledger.link_evidence_to_claim(
            research_session_id=first_session_id,
            claim_id=claim.claim_id,
            evidence_id=evidence.evidence_id,
            relation="supports",
        )
    with pytest.raises(ClaimNotFoundError):
        ledger.link_evidence_to_claim(
            research_session_id=first_session_id,
            claim_id="missing-claim",
            evidence_id=evidence.evidence_id,
            relation="supports",
        )
    with pytest.raises(EvidenceNotFoundError):
        ledger.link_evidence_to_claim(
            research_session_id=first_session_id,
            claim_id=claim.claim_id,
            evidence_id="missing-evidence",
            relation="supports",
        )
    with pytest.raises(InvalidClaimEvidenceRelationError):
        ledger.link_evidence_to_claim(
            research_session_id=first_session_id,
            claim_id=claim.claim_id,
            evidence_id=local_evidence.evidence_id,
            relation="unrelated",
        )
    assert ledger.get_claim_evidence(
        research_session_id=first_session_id, claim_id=claim.claim_id
    ) == []
