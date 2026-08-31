"""Evidence Ledger admission, provenance, ownership, and snapshot tests."""

from __future__ import annotations

import uuid

import pytest

from transit_scholar.db.engine import SessionLocal
from transit_scholar.layer3.evidence import (
    EvidenceLocator,
    PaperProvenance,
    QueryProvenance,
    ResearchEvidence,
)
from transit_scholar.layer3.execution import AgentRunService
from transit_scholar.layer3.ledger import (
    ResearchQueryLedgerService,
    ResearchQueryOwnershipError,
)
from transit_scholar.layer3.workspace import WorkspaceService


def _session_id(session) -> str:
    workspace = WorkspaceService(session).create(
        name=f"Evidence Ledger Workspace {uuid.uuid4().hex}"
    ).workspace
    run = AgentRunService(session).create_agent_run(
        workspace_id=workspace.workspace_id, user_goal="Evidence ledger test"
    )
    return AgentRunService(session).create_research_session(
        agent_run_id=run.agent_run_id, research_question="Test question"
    ).research_session_id


def _evidence(evidence_id: str, text: str = "Evidence snapshot") -> ResearchEvidence:
    return ResearchEvidence(
        evidence_id=evidence_id,
        locator=EvidenceLocator(
            workspace_id="workspace-1",
            source_kind="paper",
            paper_id="paper-1",
            block_id="block-1",
            pages=[2],
        ),
        text=text,
        source_kind="paper",
        query_provenance=QueryProvenance(query_id="retrieval-query", query_text="query"),
        paper_provenance=PaperProvenance(
            paper_id="paper-1", title="Source paper", source_uri="file:///paper.pdf"
        ),
        section="Methods",
        retrieval_provenance={"parse_run_id": "parse-v1", "source_revision": "r1"},
        rerank_provenance={"model": "deterministic"},
        final_rank=1,
    )


def test_admits_only_selected_evidence_and_reloads_provenance():
    first = SessionLocal()
    try:
        research_session_id = _session_id(first)
        ledger = ResearchQueryLedgerService(first)
        query = ledger.create_query(
            research_session_id=research_session_id, query_text="Selected evidence"
        )
        selected = ledger.admit_evidence(
            research_session_id=research_session_id,
            source_query_id=query.query_id,
            evidence=_evidence("selected"),
        )
        first.commit()
    finally:
        first.close()

    second = SessionLocal()
    try:
        ledger = ResearchQueryLedgerService(second)
        assert ledger.list_evidence(research_session_id=research_session_id) == [selected]
        reloaded = ledger.get_evidence(
            research_session_id=research_session_id, evidence_id="selected"
        )
        assert reloaded.locator.block_id == "block-1"
        assert reloaded.source_metadata["paper_provenance"]["title"] == "Source paper"
        assert reloaded.retrieval_provenance["retrieval_provenance"] == {
            "parse_run_id": "parse-v1",
            "source_revision": "r1",
        }
    finally:
        second.close()


def test_snapshot_does_not_change_when_retrieval_evidence_changes(session):
    research_session_id = _session_id(session)
    ledger = ResearchQueryLedgerService(session)
    query = ledger.create_query(research_session_id=research_session_id, query_text="Snapshot")
    retrieved = _evidence("snapshot", "Original extracted text")
    admitted = ledger.admit_evidence(
        research_session_id=research_session_id,
        source_query_id=query.query_id,
        evidence=retrieved,
    )
    retrieved.text = "Later reparse text"

    assert ledger.get_evidence(
        research_session_id=research_session_id, evidence_id=admitted.evidence_id
    ).text_snapshot == "Original extracted text"


def test_evidence_rejects_source_query_from_another_session(session):
    first_session_id = _session_id(session)
    second_session_id = _session_id(session)
    ledger = ResearchQueryLedgerService(session)
    first_query = ledger.create_query(
        research_session_id=first_session_id, query_text="First query"
    )

    with pytest.raises(ResearchQueryOwnershipError):
        ledger.admit_evidence(
            research_session_id=second_session_id,
            source_query_id=first_query.query_id,
            evidence=_evidence("cross-session"),
        )

    assert ledger.list_evidence(research_session_id=second_session_id) == []
