"""Claim Ledger persistence, explicit creation, and structural validation tests."""

from __future__ import annotations

import uuid

import pytest

from transit_scholar.db.engine import SessionLocal
from transit_scholar.layer3.execution import AgentRunService
from transit_scholar.layer3.ledger import (
    ClaimNotFoundError,
    InvalidClaimInputError,
    ResearchQueryLedgerService,
)
from transit_scholar.layer3.workspace import WorkspaceService


def _session_id(session) -> str:
    workspace = WorkspaceService(session).create(
        name=f"Claim Ledger Workspace {uuid.uuid4().hex}"
    ).workspace
    run = AgentRunService(session).create_agent_run(
        workspace_id=workspace.workspace_id, user_goal="Claim ledger test"
    )
    return AgentRunService(session).create_research_session(
        agent_run_id=run.agent_run_id, research_question="Test question"
    ).research_session_id


def test_claim_creation_status_updates_and_reload_are_explicit():
    first = SessionLocal()
    try:
        research_session_id = _session_id(first)
        ledger = ResearchQueryLedgerService(first)
        claim = ledger.create_claim(
            research_session_id=research_session_id,
            statement="Holding reduces passenger delay under this scenario.",
            rationale="Caller-created proposition",
        )
        assert claim.status == "proposed"
        for status in ("supported", "conflicting", "rejected"):
            claim = ledger.update_claim_status(
                research_session_id=research_session_id,
                claim_id=claim.claim_id,
                status=status,
            )
            assert claim.statement == "Holding reduces passenger delay under this scenario."
        first.commit()
    finally:
        first.close()

    second = SessionLocal()
    try:
        reloaded = ResearchQueryLedgerService(second).get_claim(
            research_session_id=research_session_id, claim_id=claim.claim_id
        )
        assert reloaded == claim
        assert reloaded.rationale == "Caller-created proposition"
    finally:
        second.close()


def test_claim_operations_require_existing_session_claim_and_valid_status(session):
    research_session_id = _session_id(session)
    ledger = ResearchQueryLedgerService(session)

    with pytest.raises(ClaimNotFoundError):
        ledger.update_claim_status(
            research_session_id=research_session_id,
            claim_id="missing-claim",
            status="supported",
        )
    with pytest.raises(InvalidClaimInputError):
        ledger.create_claim(
            research_session_id=research_session_id,
            statement="Valid statement",
            status="unverified",
        )
    assert ledger.list_claims(research_session_id=research_session_id) == []
