"""Frozen product commands validate authoritative status before any mutation."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from transit_scholar.db.models import AgentRun
from transit_scholar.layer3.workspace import WorkspaceService
from transit_scholar.product.research import InvalidRunCommand, ResearchService


def _snapshot(row):
    return deepcopy({column.key: getattr(row, column.key) for column in row.__table__.columns})


@pytest.mark.parametrize("command,status,allowed", [
    ("execute_run", "created", True),
    ("execute_run", "running", False),
    ("execute_run", "failed", False),
    ("execute_run", "completed", False),
    ("execute_run", "cancelled", False),
    ("resume_run", "paused", True),
    ("resume_run", "running", False),
    ("resume_run", "failed", False),
    ("resume_run", "created", False),
    ("resume_run", "completed", False),
    ("resume_run", "cancelled", False),
])
def test_command_status_contract(session, monkeypatch, command, status, allowed):
    factory = Mock()
    service = ResearchService(session, factory)
    workspace = WorkspaceService(session).create(name="command guard").workspace
    run = service.execution.create_agent_run(
        workspace_id=workspace.workspace_id, user_goal="Research transit", status=status,
    )
    conversation = service.conversations.create_session(workspace.workspace_id)
    turn = service.conversations.create_turn(
        conversation.id, "Research transit", agent_run_id=run.agent_run_id,
        resolved_user_goal=run.user_goal, status="completed",
        final_assistant_response={"answer": "Preserve this response"},
    )
    session.commit()
    run_row = session.get(AgentRun, run.agent_run_id)
    before_run = _snapshot(run_row)
    before_turn = _snapshot(turn)

    if not allowed:
        update_status = Mock(wraps=service.execution.update_agent_run_status)
        commit = Mock(wraps=session.commit)
        monkeypatch.setattr(service.execution, "update_agent_run_status", update_status)
        monkeypatch.setattr(session, "commit", commit)
        with pytest.raises(InvalidRunCommand):
            getattr(service, command)(run.agent_run_id)
        factory.build_run_scope.assert_not_called()
        update_status.assert_not_called()
        commit.assert_not_called()
        # Check both pending ORM state and a fresh authoritative database read.
        assert _snapshot(run_row) == before_run
        assert _snapshot(turn) == before_turn
        session.expire_all()
        assert service.execution.get_agent_run(run.agent_run_id).status == status
        assert _snapshot(run_row) == before_run
        assert _snapshot(turn) == before_turn
        return

    result = {"status": "completed", "final_response": {"answer": "Done"}}

    def execute(**kwargs):
        assert kwargs["agent_run_id"] == run.agent_run_id
        assert kwargs["agent_run"].agent_run_id == run.agent_run_id
        assert kwargs["user_goal"] == run.user_goal
        assert service.execution.get_agent_run(run.agent_run_id).status == "running"
        return result

    runtime = SimpleNamespace(execute=Mock(side_effect=execute))
    scope = SimpleNamespace(run_research_runtime=runtime, close=Mock())
    factory.build_run_scope.return_value = scope
    assert getattr(service, command)(run.agent_run_id) == result
    factory.build_run_scope.assert_called_once_with(run.agent_run_id)
    runtime.execute.assert_called_once()
    scope.close.assert_called_once()
    assert service.execution.get_agent_run(run.agent_run_id).status == "completed"
    assert service.conversations.get_turn(turn.id).final_assistant_response == result["final_response"]
