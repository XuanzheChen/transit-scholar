"""Research Query Ledger persistence and structural validation tests."""

from __future__ import annotations

import uuid

import pytest

from transit_scholar.db.engine import SessionLocal
from transit_scholar.layer3.execution import AgentRunService
from transit_scholar.layer3.ledger import (
    ResearchQueryLedgerService,
    ResearchQueryNotFoundError,
    ResearchQueryOwnershipError,
)
from transit_scholar.layer3.workspace import WorkspaceService


def _session_id(session) -> str:
    workspace = WorkspaceService(session).create(
        name=f"Ledger Workspace {uuid.uuid4().hex}"
    ).workspace
    run = AgentRunService(session).create_agent_run(
        workspace_id=workspace.workspace_id, user_goal="Research ledger test"
    )
    return AgentRunService(session).create_research_session(
        agent_run_id=run.agent_run_id, research_question="Test question"
    ).research_session_id


def test_query_persists_and_reloads_from_a_fresh_session():
    first = SessionLocal()
    try:
        research_session_id = _session_id(first)
        created = ResearchQueryLedgerService(first).create_query(
            research_session_id=research_session_id,
            query_text="What bus holding methods were evaluated?",
        )
        first.commit()
    finally:
        first.close()

    second = SessionLocal()
    try:
        reloaded = ResearchQueryLedgerService(second).get_query(
            research_session_id=research_session_id, query_id=created.query_id
        )
        assert reloaded == created
        assert reloaded.status == "active"
    finally:
        second.close()


def test_completed_and_abandoned_queries_remain_persisted():
    first = SessionLocal()
    try:
        research_session_id = _session_id(first)
        ledger = ResearchQueryLedgerService(first)
        completed = ledger.create_query(
            research_session_id=research_session_id, query_text="Completed query"
        )
        abandoned = ledger.create_query(
            research_session_id=research_session_id, query_text="Abandoned query"
        )
        ledger.update_query_status(
            research_session_id=research_session_id,
            query_id=completed.query_id,
            status="completed",
        )
        ledger.update_query_status(
            research_session_id=research_session_id,
            query_id=abandoned.query_id,
            status="abandoned",
        )
        first.commit()
    finally:
        first.close()

    second = SessionLocal()
    try:
        queries = ResearchQueryLedgerService(second).list_queries(
            research_session_id=research_session_id
        )
        assert {query.query_id for query in queries} == {
            completed.query_id,
            abandoned.query_id,
        }
        assert {query.status for query in queries} == {"completed", "abandoned"}
    finally:
        second.close()


def test_parent_query_must_exist_in_the_same_session(session):
    first_session_id = _session_id(session)
    second_session_id = _session_id(session)
    ledger = ResearchQueryLedgerService(session)
    parent = ledger.create_query(
        research_session_id=first_session_id, query_text="Parent query"
    )
    child = ledger.create_query(
        research_session_id=first_session_id,
        query_text="Child query",
        parent_query_id=parent.query_id,
    )
    assert child.parent_query_id == parent.query_id

    with pytest.raises(ResearchQueryOwnershipError):
        ledger.create_query(
            research_session_id=second_session_id,
            query_text="Cross-session child",
            parent_query_id=parent.query_id,
        )
    with pytest.raises(ResearchQueryNotFoundError):
        ledger.create_query(
            research_session_id=first_session_id,
            query_text="Missing-parent child",
            parent_query_id="missing-parent",
        )
