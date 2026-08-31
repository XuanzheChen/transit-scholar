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
    InvalidEvidenceInputError,
    ResearchQueryLedgerService,
    ResearchQueryOwnershipError,
)
from transit_scholar.layer3.workspace import WorkspaceService


def _session_context(session) -> tuple[str, str]:
    workspace = WorkspaceService(session).create(
        name=f"Evidence Ledger Workspace {uuid.uuid4().hex}"
    ).workspace
    run = AgentRunService(session).create_agent_run(
        workspace_id=workspace.workspace_id, user_goal="Evidence ledger test"
    )
    research_session_id = AgentRunService(session).create_research_session(
        agent_run_id=run.agent_run_id, research_question="Test question"
    ).research_session_id
    return research_session_id, workspace.workspace_id


def _evidence(
    evidence_id: str,
    workspace_id: str,
    query_id: str,
    text: str = "Evidence snapshot",
    session_id: str | None = None,
) -> ResearchEvidence:
    return ResearchEvidence(
        evidence_id=evidence_id,
        locator=EvidenceLocator(
            workspace_id=workspace_id,
            source_kind="paper",
            paper_id="paper-1",
            block_id="block-1",
            pages=[2],
        ),
        text=text,
        source_kind="paper",
        query_provenance=QueryProvenance(
            query_id=query_id, session_id=session_id, query_text="query"
        ),
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
        research_session_id, workspace_id = _session_context(first)
        ledger = ResearchQueryLedgerService(first)
        query = ledger.create_query(
            research_session_id=research_session_id, query_text="Selected evidence"
        )
        selected = ledger.admit_evidence(
            research_session_id=research_session_id,
            source_query_id=query.query_id,
            evidence=_evidence(
                "selected", workspace_id, query.query_id, session_id=research_session_id
            ),
        )
        first.commit()
    finally:
        first.close()

    second = SessionLocal()
    try:
        ledger = ResearchQueryLedgerService(second)
        assert ledger.list_evidence(research_session_id=research_session_id) == [selected]
        reloaded = ledger.get_evidence(
            research_session_id=research_session_id, evidence_id=selected.evidence_id
        )
        assert reloaded.evidence_id != "selected"
        assert reloaded.retrieval_provenance["retrieval_evidence_id"] == "selected"
        assert reloaded.locator.block_id == "block-1"
        assert reloaded.source_metadata["paper_provenance"]["title"] == "Source paper"
        assert reloaded.retrieval_provenance["retrieval_provenance"] == {
            "parse_run_id": "parse-v1",
            "source_revision": "r1",
        }
    finally:
        second.close()


def test_snapshot_does_not_change_when_retrieval_evidence_changes(session):
    research_session_id, workspace_id = _session_context(session)
    ledger = ResearchQueryLedgerService(session)
    query = ledger.create_query(research_session_id=research_session_id, query_text="Snapshot")
    retrieved = _evidence(
        "snapshot", workspace_id, query.query_id, "Original extracted text"
    )
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
    first_session_id, first_workspace_id = _session_context(session)
    second_session_id, _ = _session_context(session)
    ledger = ResearchQueryLedgerService(session)
    first_query = ledger.create_query(
        research_session_id=first_session_id, query_text="First query"
    )

    with pytest.raises(ResearchQueryOwnershipError):
        ledger.admit_evidence(
            research_session_id=second_session_id,
            source_query_id=first_query.query_id,
            evidence=_evidence(
                "cross-session", first_workspace_id, first_query.query_id
            ),
        )

    assert ledger.list_evidence(research_session_id=second_session_id) == []


def test_same_retrieval_evidence_id_can_be_admitted_in_two_sessions(session):
    first_session_id, first_workspace_id = _session_context(session)
    second_session_id, second_workspace_id = _session_context(session)
    ledger = ResearchQueryLedgerService(session)
    first_query = ledger.create_query(
        research_session_id=first_session_id, query_text="First query"
    )
    second_query = ledger.create_query(
        research_session_id=second_session_id, query_text="Second query"
    )

    first = ledger.admit_evidence(
        research_session_id=first_session_id,
        source_query_id=first_query.query_id,
        evidence=_evidence(
            "shared-retrieval-id", first_workspace_id, first_query.query_id
        ),
    )
    second = ledger.admit_evidence(
        research_session_id=second_session_id,
        source_query_id=second_query.query_id,
        evidence=_evidence(
            "shared-retrieval-id", second_workspace_id, second_query.query_id
        ),
    )

    assert first.evidence_id != second.evidence_id
    assert first.retrieval_provenance["retrieval_evidence_id"] == "shared-retrieval-id"
    assert second.retrieval_provenance["retrieval_evidence_id"] == "shared-retrieval-id"


@pytest.mark.parametrize(
    ("provenance_change", "message"),
    [
        ("query", "query provenance"),
        ("session", "session provenance"),
        ("workspace", "workspace provenance"),
        ("paper", "paper provenance"),
    ],
)
def test_evidence_rejects_mismatched_provenance(
    session, provenance_change: str, message: str
):
    research_session_id, workspace_id = _session_context(session)
    ledger = ResearchQueryLedgerService(session)
    query = ledger.create_query(
        research_session_id=research_session_id, query_text="Provenance query"
    )
    evidence = _evidence(
        "mismatch", workspace_id, query.query_id, session_id=research_session_id
    )
    if provenance_change == "query":
        evidence.query_provenance.query_id = "another-query"
    elif provenance_change == "session":
        evidence.query_provenance.session_id = "another-session"
    elif provenance_change == "workspace":
        evidence.locator.workspace_id = "another-workspace"
    else:
        evidence.paper_provenance.paper_id = "another-paper"

    with pytest.raises(InvalidEvidenceInputError, match=message):
        ledger.admit_evidence(
            research_session_id=research_session_id,
            source_query_id=query.query_id,
            evidence=evidence,
        )

    assert ledger.list_evidence(research_session_id=research_session_id) == []
