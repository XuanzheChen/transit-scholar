"""Stage4 public services keep ledger records outside ResearchState payloads."""

from __future__ import annotations

import uuid

from transit_scholar.layer3.evidence import EvidenceLocator, ResearchEvidence
from transit_scholar.layer3.execution import AgentRunService
from transit_scholar.layer3.ledger import (
    ClaimEvidenceLinkService,
    ClaimService,
    EvidenceService,
    QueryService,
)
from transit_scholar.layer3.state import ResearchStateService
from transit_scholar.layer3.workspace import WorkspaceService


def _run_and_research_session(session):
    workspace_id = WorkspaceService(session).create(
        name=f"Stage4 service workspace {uuid.uuid4().hex}"
    ).workspace.workspace_id
    execution = AgentRunService(session)
    run = execution.create_agent_run(
        workspace_id=workspace_id, user_goal="Ledger service boundary test"
    )
    research_session = execution.create_research_session(
        agent_run_id=run.agent_run_id, research_question="Test question"
    )
    return run, research_session


def test_public_services_persist_ledger_independently_of_research_state(session):
    run, research_session = _run_and_research_session(session)
    state_payload = {"current_focus": "collect evidence", "checkpoint": 1}
    ResearchStateService(session).save_research_state(
        agent_run_id=run.agent_run_id,
        research_session_id=research_session.research_session_id,
        payload=state_payload,
    )

    queries = QueryService(session)
    evidence_service = EvidenceService(session)
    claims = ClaimService(session)
    links = ClaimEvidenceLinkService(session)
    query = queries.create_query(
        research_session_id=research_session.research_session_id,
        query_text="Which evidence supports holding?",
    )
    evidence = evidence_service.admit_evidence(
        research_session_id=research_session.research_session_id,
        source_query_id=query.query_id,
        evidence=ResearchEvidence(
            evidence_id="evidence-1",
            locator=EvidenceLocator(
                workspace_id=run.workspace_id, source_kind="paper", paper_id="paper-1"
            ),
            text="Explicitly admitted evidence",
            source_kind="paper",
        ),
    )
    claim = claims.create_claim(
        research_session_id=research_session.research_session_id,
        statement="Holding has support in this source.",
    )
    link = links.link_evidence_to_claim(
        research_session_id=research_session.research_session_id,
        claim_id=claim.claim_id,
        evidence_id=evidence.evidence_id,
        relation="supports",
    )

    assert ResearchStateService(session).load_research_state(
        agent_run_id=run.agent_run_id,
        research_session_id=research_session.research_session_id,
    ).payload == state_payload
    assert queries.list_queries(research_session_id=research_session.research_session_id) == [
        query
    ]
    assert evidence_service.get_evidence(
        research_session_id=research_session.research_session_id,
        evidence_id=evidence.evidence_id,
    ) == evidence
    assert claims.read_claim(
        research_session_id=research_session.research_session_id, claim_id=claim.claim_id
    ) == claim
    assert links.get_claim_evidence(
        research_session_id=research_session.research_session_id, claim_id=claim.claim_id
    ) == [link]


def test_public_services_require_no_agent_runtime_or_semantic_provider(session):
    _, research_session = _run_and_research_session(session)

    query = QueryService(session).create_query(
        research_session_id=research_session.research_session_id,
        query_text="Caller supplied query",
    )
    claim = ClaimService(session).create_claim(
        research_session_id=research_session.research_session_id,
        statement="Caller supplied claim",
    )

    assert query.status == "active"
    assert claim.status == "proposed"
