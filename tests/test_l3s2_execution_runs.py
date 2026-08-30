"""Layer3 Stage2 AgentRun and ResearchSession persistence tests."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from transit_scholar.db.engine import SessionLocal
from transit_scholar.db.models import AgentRun, Workspace
from transit_scholar.layer3.execution import (
    AgentRunService,
    ResearchSessionOwnershipError,
)
from transit_scholar.layer3.workspace import (
    WorkspaceNotActiveError,
    WorkspaceNotFoundError,
    WorkspaceService,
)


def _active_workspace(session, workspace_id: str | None = None) -> str:
    return WorkspaceService(session).create(
        name="Execution Workspace", workspace_id=workspace_id or uuid.uuid4().hex
    ).workspace.workspace_id


def test_agent_run_persists_workspace_revision_across_fresh_session():
    first = SessionLocal()
    try:
        workspace_id = _active_workspace(first)
        created = AgentRunService(first).create_agent_run(
            workspace_id=workspace_id,
            user_goal="Compare bus holding strategies",
        )
        first.commit()
    finally:
        first.close()

    second = SessionLocal()
    try:
        reloaded = AgentRunService(second).get_agent_run(created.agent_run_id)
        assert reloaded == created
        assert reloaded.workspace_id == workspace_id
        assert reloaded.workspace_revision == 1
        assert reloaded.status == "created"
    finally:
        second.close()


def test_agent_run_requires_an_active_workspace(session):
    service = AgentRunService(session)
    existing_runs = session.execute(select(AgentRun)).scalars().all()
    with pytest.raises(WorkspaceNotFoundError):
        service.create_agent_run(workspace_id="missing", user_goal="Question")

    for index, status in enumerate(("archived", "deleting", "deleted")):
        workspace_id = _active_workspace(session, f"{index + 1:032d}")
        session.get(Workspace, workspace_id).status = status
        session.flush()
        with pytest.raises(WorkspaceNotActiveError):
            service.create_agent_run(workspace_id=workspace_id, user_goal="Question")

    assert session.execute(select(AgentRun)).scalars().all() == existing_runs


def test_sessions_are_ordered_owned_and_plan_free(session):
    workspace_id = _active_workspace(session)
    service = AgentRunService(session)
    run = service.create_agent_run(workspace_id=workspace_id, user_goal="Broader goal")
    first = service.create_research_session(
        agent_run_id=run.agent_run_id, research_question="Question one"
    )
    second = service.create_research_session(
        agent_run_id=run.agent_run_id, research_question="Question two"
    )

    sessions = service.list_research_sessions(run.agent_run_id)
    assert [item.research_session_id for item in sessions] == sorted(
        [first.research_session_id, second.research_session_id]
    )
    assert all(item.agent_run_id == run.agent_run_id for item in sessions)
    assert "plan_id" not in AgentRun.__table__.columns
    assert "plan_id" not in type(first).model_fields


def test_session_read_and_mutation_reject_wrong_run_boundary(session):
    workspace_id = _active_workspace(session)
    service = AgentRunService(session)
    owner = service.create_agent_run(workspace_id=workspace_id, user_goal="Owner")
    other = service.create_agent_run(workspace_id=workspace_id, user_goal="Other")
    owned_session = service.create_research_session(
        agent_run_id=owner.agent_run_id, research_question="Owned question"
    )

    with pytest.raises(ResearchSessionOwnershipError):
        service.get_research_session(other.agent_run_id, owned_session.research_session_id)
    with pytest.raises(ResearchSessionOwnershipError):
        service.update_research_session_status(
            other.agent_run_id, owned_session.research_session_id, "running"
        )

    assert service.update_research_session_status(
        owner.agent_run_id, owned_session.research_session_id, "running"
    ).status == "running"
