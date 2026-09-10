"""Product/Core crash boundaries exercised against durable, independent sessions."""
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from transit_scholar.api import create_app
from transit_scholar.db.models import AgentRun, ConversationTurn, Paper
from transit_scholar.layer3.planning import RunDecision
from transit_scholar.layer3.trace import AgentTraceService
from test_freeze_runtime_integration import context_for, freeze_root


class ProcessLoss(BaseException):
    """Escape ordinary application exception handlers like process death."""


def prepared(context):
    product = context.create_product()
    workspace = product.create_workspace('Crash recovery')
    conversation = product.create_conversation(workspace.workspace_id, 'Recovery')
    product.session.commit()
    message = product.prepare_message(conversation.id, 'Recover the answer')
    return product, workspace.workspace_id, conversation.id, message


def test_startup_fails_prepared_unscheduled_admission_without_execution(freeze_root, monkeypatch):
    context = context_for(freeze_root)
    product, workspace, conversation, message = prepared(context)
    product.close()  # crash after prepare_message COMMIT, before submit_reserved
    def never_execute(*args, **kwargs):
        pytest.fail('startup must not construct a research runtime')
    monkeypatch.setattr(context.runtime_factory, 'build_run_scope', never_execute)
    try:
        with TestClient(create_app(runtime_context=context)) as client:
            with context.session_factory() as fresh:
                assert fresh.get(AgentRun, message.agent_run_id).status == 'failed'
                turn = fresh.get(ConversationTurn, message.turn_id)
                assert turn.status == 'failed'
                assert turn.error_message == 'Research admission was interrupted before execution.'
            assert client.post(f'/api/v1/workspaces/{workspace}/archive').status_code == 200
            again = context.create_product()
            try:
                assert again.reconcile_interrupted_runs() == []
            finally:
                again.close()
    finally:
        context.session_factory.kw['bind'].dispose()


@pytest.mark.parametrize('artifact_state', ['valid', 'missing', 'corrupt'])
def test_startup_recovers_completed_run_turn_from_checkpoint(freeze_root, monkeypatch, artifact_state):
    context = context_for(freeze_root, coordinator=lambda _: RunDecision(mode='complete', completion_reason='done'))
    product, workspace, conversation, message = prepared(context)
    def crash(*args):
        raise ProcessLoss()
    monkeypatch.setattr(product.research, '_sync_linked_turn', crash)
    try:
        with pytest.raises(ProcessLoss):
            product.execute_run(message.agent_run_id)
        product.close()
        with context.session_factory() as fresh:
            assert fresh.get(AgentRun, message.agent_run_id).status == 'completed'
            assert fresh.get(ConversationTurn, message.turn_id).status == 'running'
            original_trace = [(e.event_id, e.event_type) for e in AgentTraceService(fresh).read_trace(agent_run_id=message.agent_run_id)]
        checkpoint = context.runtime_factory.runtime_root / message.agent_run_id / 'run_state.json'
        if artifact_state == 'missing':
            checkpoint.unlink()
        elif artifact_state == 'corrupt':
            checkpoint.write_text('{private broken checkpoint', encoding='utf-8')
        # Recovery must work even when no provider/runtime can be composed.
        context.runtime_factory = None
        for _ in range(2):
            with TestClient(create_app(runtime_context=context)) as client:
                view = client.get(f'/api/v1/conversations/{conversation}').json()['turns'][0]
                assert view['agent_run_id'] == message.agent_run_id
                if artifact_state == 'valid':
                    assert view['status'] == 'completed'
                    assert view['final_answer'] == 'Recover the answer'
                else:
                    assert view['status'] == 'failed'
                    assert view['error_message'] == 'TURN_RECOVERY_FAILED: Durable final response is unavailable.'
                    assert 'private' not in str(view)
                with context.session_factory() as fresh:
                    assert fresh.get(AgentRun, message.agent_run_id).status == 'completed'
                    assert [(e.event_id, e.event_type) for e in AgentTraceService(fresh).read_trace(agent_run_id=message.agent_run_id)] == original_trace
                    assert len(fresh.scalars(select(ConversationTurn)).all()) == 1
    finally:
        product.close()
        context.session_factory.kw['bind'].dispose()


@pytest.mark.parametrize('status', ['failed', 'cancelled'])
def test_startup_reconciles_terminal_failure_turn_idempotently(freeze_root, status):
    context = context_for(freeze_root)
    product, _, _, message = prepared(context)
    product.research.execution.update_agent_run_status(message.agent_run_id, status)
    product.session.commit()  # persisted Core terminal outcome, no Turn sync
    product.close()
    try:
        with TestClient(create_app(runtime_context=context)):
            with context.session_factory() as fresh:
                assert fresh.get(AgentRun, message.agent_run_id).status == status
                assert fresh.get(ConversationTurn, message.turn_id).status == 'failed'
            again = context.create_product()
            try:
                assert again.reconcile_interrupted_runs() == []
            finally:
                again.close()
    finally:
        context.session_factory.kw['bind'].dispose()


def test_turn_flush_failure_rolls_back_without_changing_completed_run(freeze_root, monkeypatch):
    context = context_for(freeze_root, coordinator=lambda _: RunDecision(mode='complete', completion_reason='done'))
    product, _, conversation, message = prepared(context)
    product.session.add(Paper(id='duplicate', title='Existing'))
    product.session.commit()
    original_update = product.conversations.update_turn
    def fail_final_flush(turn_id, **values):
        if values.get('status') == 'completed':
            product.session.add(Paper(id='duplicate', title='Trigger actual SQL flush failure'))
        return original_update(turn_id, **values)
    monkeypatch.setattr(product.conversations, 'update_turn', fail_final_flush)
    try:
        result = product.execute_run(message.agent_run_id)
        assert result['status'] == 'completed'
        assert product.session.is_active
        with context.session_factory() as fresh:
            assert fresh.get(AgentRun, message.agent_run_id).status == 'completed'
            assert fresh.get(ConversationTurn, message.turn_id).status == 'running'
            events = [e.event_type for e in AgentTraceService(fresh).read_trace(agent_run_id=message.agent_run_id)]
            assert events.count('run.completed') == 1
            assert 'run.failed' not in events
        product.close()
        with TestClient(create_app(runtime_context=context)) as client:
            assert client.get(f'/api/v1/conversations/{conversation}').json()['turns'][0]['status'] == 'completed'
    finally:
        product.close()
        context.session_factory.kw['bind'].dispose()
