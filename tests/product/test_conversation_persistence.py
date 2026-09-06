import pytest
from sqlalchemy.orm import Session

from transit_scholar.db.models import AgentRun, ResearchSession, Workspace
from transit_scholar.layer3.workspace import WorkspaceService
from transit_scholar.product.conversation import ConversationGoalResolver, ConversationService


def make_workspace(session, name="workspace"):
    return WorkspaceService(session).create(name=name).workspace.workspace_id


def test_session_and_turns_persist_and_reload(session):
    workspace_id = make_workspace(session)
    service = ConversationService(session)
    conversation = service.create_session(workspace_id, title="Research")
    first = service.create_turn(
        conversation.id,
        "Find papers",
        resolved_user_goal="Find papers about transit",
        agent_run_id="run-1",
        final_assistant_response={"answer": "done"},
        status="completed",
    )
    second = service.create_turn(conversation.id, "Compare them", status="running")
    fresh = Session(bind=session.connection())
    loaded = fresh.get(type(conversation), conversation.id)
    turns = list(fresh.query(type(first)).filter_by(conversation_id=conversation.id).order_by(type(first).sequence))
    fresh.close()
    assert loaded.workspace_id == workspace_id
    assert [turn.sequence for turn in turns] == [1, 2]
    assert turns[0].user_message == "Find papers"
    assert turns[0].final_assistant_response == {"answer": "done"}
    assert first.sequence == 1 and second.sequence == 2


def test_invalid_or_inactive_workspace_rejected(session):
    service = ConversationService(session)
    with pytest.raises(ValueError):
        service.create_session("missing")
    workspace_id = make_workspace(session)
    workspace = session.get(Workspace, workspace_id)
    workspace.status = "archived"
    session.flush()
    with pytest.raises(ValueError):
        service.create_session(workspace_id)


def test_recent_completed_context_is_bounded_and_isolated(session):
    workspace_a = make_workspace(session, "a")
    workspace_b = make_workspace(session, "b")
    service = ConversationService(session, recent_limit=2)
    conversation_a = service.create_session(workspace_a)
    conversation_b = service.create_session(workspace_b)
    service.create_turn(conversation_a.id, "one", status="completed")
    service.create_turn(conversation_a.id, "two", status="failed")
    service.create_turn(conversation_a.id, "three", status="completed")
    service.create_turn(conversation_a.id, "four", status="running")
    service.create_turn(conversation_b.id, "other", status="completed")
    assert [turn.user_message for turn in service.recent_completed(conversation_a.id)] == ["one", "three"]
    assert service.recent_completed(conversation_a.id, limit=1)[0].user_message == "three"


def test_goal_resolver_is_standalone_and_side_effect_free(session):
    workspace_id = make_workspace(session)
    service = ConversationService(session)
    conversation = service.create_session(workspace_id)
    prior = service.create_turn(
        conversation.id,
        "Which paper is second?",
        resolved_user_goal="Identify the second paper in the list",
        final_assistant_response={"papers": ["A", "B"]},
        status="completed",
    )
    goal = ConversationGoalResolver().resolve("Summarize that one", [prior])
    assert goal and "Summarize that one" in goal and "second paper" in goal
    assert session.query(AgentRun).count() == 0
    assert session.query(ResearchSession).count() == 0


def test_core_execution_models_have_no_conversation_ownership_fields():
    assert "conversation_id" not in AgentRun.__table__.columns
    assert "turn_id" not in AgentRun.__table__.columns
    assert "conversation_id" not in ResearchSession.__table__.columns
    assert "turn_id" not in ResearchSession.__table__.columns
