"""Layer3 Stage2 recoverable ResearchState persistence tests."""

from __future__ import annotations

import uuid

import pytest

from transit_scholar.db.engine import SessionLocal
from transit_scholar.layer3.execution import AgentRunService, ResearchSessionOwnershipError
from transit_scholar.layer3.state import ResearchStateService
from transit_scholar.layer3.workspace import WorkspaceService


def _run_with_sessions(session):
    workspace_id = WorkspaceService(session).create(
        name="Research state workspace", workspace_id=uuid.uuid4().hex
    ).workspace.workspace_id
    execution = AgentRunService(session)
    run = execution.create_agent_run(workspace_id=workspace_id, user_goal="Research goal")
    first = execution.create_research_session(
        agent_run_id=run.agent_run_id, research_question="First question"
    )
    second = execution.create_research_session(
        agent_run_id=run.agent_run_id, research_question="Second question"
    )
    return run, first, second


def test_research_state_reloads_across_fresh_database_session():
    first_session = SessionLocal()
    try:
        run, research_session, _ = _run_with_sessions(first_session)
        payload = {
            "current_focus": "Compare headway control methods",
            "recent_operations": ["search:methodology"],
            "evidence_locators": [{"paper_id": "paper-1", "page": 3}],
            "open_questions": ["How does demand variability change the outcome?"],
            "future_runtime_data": {"checkpoint": 2},
        }
        saved = ResearchStateService(first_session).save_research_state(
            agent_run_id=run.agent_run_id,
            research_session_id=research_session.research_session_id,
            payload=payload,
        )
        first_session.commit()
    finally:
        first_session.close()

    second_session = SessionLocal()
    try:
        reloaded = ResearchStateService(second_session).load_research_state(
            agent_run_id=run.agent_run_id,
            research_session_id=research_session.research_session_id,
        )
        assert reloaded is not None
        assert reloaded.research_session_id == saved.research_session_id
        assert reloaded.payload == payload
    finally:
        second_session.close()


def test_research_states_are_isolated_between_sessions(session):
    run, first, second = _run_with_sessions(session)
    states = ResearchStateService(session)
    states.save_research_state(
        agent_run_id=run.agent_run_id,
        research_session_id=first.research_session_id,
        payload={"current_focus": "First only"},
    )
    states.save_research_state(
        agent_run_id=run.agent_run_id,
        research_session_id=second.research_session_id,
        payload={"current_focus": "Second only"},
    )

    assert states.load_research_state(
        agent_run_id=run.agent_run_id, research_session_id=first.research_session_id
    ).payload == {"current_focus": "First only"}
    assert states.load_research_state(
        agent_run_id=run.agent_run_id, research_session_id=second.research_session_id
    ).payload == {"current_focus": "Second only"}


def test_state_is_plan_claim_memory_and_runtime_framework_free(session):
    run, research_session, _ = _run_with_sessions(session)
    saved = ResearchStateService(session).save_research_state(
        agent_run_id=run.agent_run_id,
        research_session_id=research_session.research_session_id,
        payload={"current_focus": "Gather source references", "custom": {"step": 1}},
    )

    assert saved.payload == {
        "current_focus": "Gather source references",
        "custom": {"step": 1},
    }
    assert "plan_id" not in type(saved).model_fields
    assert "claim" not in type(saved).model_fields
    assert "memory" not in type(saved).model_fields
    assert "langgraph" not in type(saved).model_fields


def test_state_access_rejects_the_wrong_agent_run(session):
    workspace_id = WorkspaceService(session).create(
        name="Ownership workspace", workspace_id=uuid.uuid4().hex
    ).workspace.workspace_id
    execution = AgentRunService(session)
    owner = execution.create_agent_run(workspace_id=workspace_id, user_goal="Owner")
    other = execution.create_agent_run(workspace_id=workspace_id, user_goal="Other")
    research_session = execution.create_research_session(
        agent_run_id=owner.agent_run_id, research_question="Owned question"
    )
    states = ResearchStateService(session)

    with pytest.raises(ResearchSessionOwnershipError):
        states.save_research_state(
            agent_run_id=other.agent_run_id,
            research_session_id=research_session.research_session_id,
            payload={"current_focus": "Must not save"},
        )
    with pytest.raises(ResearchSessionOwnershipError):
        states.load_research_state(
            agent_run_id=other.agent_run_id,
            research_session_id=research_session.research_session_id,
        )
