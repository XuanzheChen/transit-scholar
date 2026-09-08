"""Product preparation persists linked identities before execution begins."""

from types import SimpleNamespace
from unittest.mock import Mock

from sqlalchemy.orm import Session

from transit_scholar.db.models import AgentRun, ConversationTurn
from transit_scholar.layer3.workspace import WorkspaceService
from transit_scholar.product.research import ResearchService


def _conversation(service, session):
    workspace = WorkspaceService(session).create(name="prompt preparation").workspace
    return service.conversations.create_session(workspace.workspace_id)


def test_prepare_message_commits_linked_turn_and_run_before_execution(session):
    runtime_factory = Mock()
    service = ResearchService(session, runtime_factory)
    conversation = _conversation(service, session)

    prepared = service.prepare_message(conversation.id, "Research transit demand")

    runtime_factory.build_run_scope.assert_not_called()
    fresh = Session(bind=session.connection())
    try:
        turn = fresh.get(ConversationTurn, prepared.turn_id)
        run = fresh.get(AgentRun, prepared.agent_run_id)
        assert turn is not None
        assert run is not None
        assert turn.agent_run_id == prepared.agent_run_id
        assert turn.resolved_user_goal == prepared.resolved_user_goal
        assert turn.status == "preparing"
        assert run.status == "created"
        assert run.user_goal == prepared.resolved_user_goal
    finally:
        fresh.close()


def test_submit_message_composes_preparation_and_execution(session):
    runtime_factory = Mock()
    service = ResearchService(session, runtime_factory)
    conversation = _conversation(service, session)
    result = {"status": "completed", "final_response": {"answer": "Completed response"}}
    runtime = SimpleNamespace(execute=Mock(return_value=result))
    scope = SimpleNamespace(run_research_runtime=runtime, close=Mock())
    runtime_factory.build_run_scope.return_value = scope

    turn = service.submit_message(conversation.id, "Research transit demand")

    assert turn.status == "completed"
    assert turn.agent_run_id is not None
    assert turn.final_assistant_response == result["final_response"]
    runtime.execute.assert_called_once()
    assert service.execution.get_agent_run(turn.agent_run_id).status == "completed"
