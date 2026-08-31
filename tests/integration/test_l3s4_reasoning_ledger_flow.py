"""End-to-end persistence coverage for the Stage4 reasoning ledger."""

from __future__ import annotations

import uuid

from transit_scholar.db.engine import SessionLocal
from transit_scholar.layer3.evidence import EvidenceLocator, ResearchEvidence
from transit_scholar.layer3.execution import AgentRunService
from transit_scholar.layer3.ledger import (
    ClaimEvidenceLinkService,
    ClaimService,
    EvidenceService,
    QueryService,
)
from transit_scholar.layer3.workspace import WorkspaceService


def _research_session_id(session) -> str:
    workspace = WorkspaceService(session).create(
        name=f"L3S4 integration workspace {uuid.uuid4().hex}"
    ).workspace
    agent_run = AgentRunService(session).create_agent_run(
        workspace_id=workspace.workspace_id,
        user_goal="Verify the Stage4 reasoning ledger lifecycle",
    )
    return AgentRunService(session).create_research_session(
        agent_run_id=agent_run.agent_run_id,
        research_question="What does the admitted evidence establish?",
    ).research_session_id


def _evidence(evidence_id: str, text: str) -> ResearchEvidence:
    return ResearchEvidence(
        evidence_id=evidence_id,
        locator=EvidenceLocator(
            workspace_id="integration-workspace",
            source_kind="paper",
            paper_id="integration-paper",
            block_id=f"block-{evidence_id}",
        ),
        text=text,
        source_kind="paper",
        retrieval_provenance={"parse_run_id": "parse-run-1", "revision": "v1"},
    )


def test_query_evidence_claim_ledger_reconstructs_after_fresh_session():
    first = SessionLocal()
    try:
        research_session_id = _research_session_id(first)
        queries = QueryService(first)
        evidence_service = EvidenceService(first)
        claims = ClaimService(first)
        links = ClaimEvidenceLinkService(first)

        query = queries.create_query(
            research_session_id=research_session_id,
            query_text="Does the intervention reduce delay?",
        )
        supporting = evidence_service.admit_evidence(
            research_session_id=research_session_id,
            source_query_id=query.query_id,
            evidence=_evidence("supports-delay", "The intervention reduced delay."),
        )
        contradicting = evidence_service.admit_evidence(
            research_session_id=research_session_id,
            source_query_id=query.query_id,
            evidence=_evidence(
                "contradicts-delay", "The intervention increased delay at peak demand."
            ),
        )
        claim = claims.create_claim(
            research_session_id=research_session_id,
            statement="The intervention reduces delay.",
        )
        support_link = links.link_evidence_to_claim(
            research_session_id=research_session_id,
            claim_id=claim.claim_id,
            evidence_id=supporting.evidence_id,
            relation="supports",
        )
        contradiction_link = links.link_evidence_to_claim(
            research_session_id=research_session_id,
            claim_id=claim.claim_id,
            evidence_id=contradicting.evidence_id,
            relation="contradicts",
        )
        first.commit()
    finally:
        first.close()

    second = SessionLocal()
    try:
        queries = QueryService(second)
        evidence_service = EvidenceService(second)
        claims = ClaimService(second)
        links = ClaimEvidenceLinkService(second)

        assert queries.get_query(
            research_session_id=research_session_id, query_id=query.query_id
        ) == query
        reloaded_evidence = evidence_service.list_evidence(
            research_session_id=research_session_id
        )
        assert {record.evidence_id for record in reloaded_evidence} == {
            supporting.evidence_id,
            contradicting.evidence_id,
        }
        assert claims.get_claim(
            research_session_id=research_session_id, claim_id=claim.claim_id
        ) == claim
        reloaded_links = links.get_claim_evidence(
            research_session_id=research_session_id, claim_id=claim.claim_id
        )
        assert {(link.evidence_id, link.relation) for link in reloaded_links} == {
            (support_link.evidence_id, support_link.relation),
            (contradiction_link.evidence_id, contradiction_link.relation),
        }
    finally:
        second.close()
