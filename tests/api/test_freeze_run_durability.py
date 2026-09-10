"""New-session durability assertions at Runtime return, before scope.close."""
import pytest

from transit_scholar.db.models import AgentRun
from transit_scholar.layer3.execution import AgentRunService
from transit_scholar.layer3.planning import RunDecision
from transit_scholar.layer3.trace import AgentTraceService
from test_freeze_runtime_integration import context_for, freeze_root


@pytest.mark.parametrize('mode,expected,event', [
    ('complete', 'completed', 'run.completed'),
    ('cancel', 'cancelled', 'run.failed'),
    ('limit', 'failed', 'run.failed'),
    ('error', 'failed', 'run.failed'),
    ('pause', 'paused', 'run.paused'),
])
def test_runtime_returns_only_after_lifecycle_and_trace_commit(freeze_root, mode, expected, event):
    def coordinator(_):
        if mode == 'error':
            raise ValueError('private provider error')
        return RunDecision(mode='complete', completion_reason='done')
    context = context_for(freeze_root, coordinator=coordinator)
    product = context.create_product()
    workspace = product.create_workspace('Durability')
    run = AgentRunService(product.session).create_agent_run(workspace_id=workspace.workspace_id, user_goal='goal', status='running')
    product.session.commit()
    scope = context.runtime_factory.build_run_scope(run.agent_run_id)
    try:
        if mode == 'pause':
            context.runtime_factory.run_control.request_pause(run.agent_run_id)
        if mode == 'cancel':
            scope.run_runtime.is_cancelled = lambda: True
        if mode == 'limit':
            scope.run_runtime.config = scope.run_runtime.config.model_copy(update={"max_run_steps": 0})
        if mode == 'error':
            with pytest.raises(ValueError, match='private provider error'):
                scope.run_runtime.execute(agent_run_id=run.agent_run_id)
        else:
            result = scope.run_runtime.execute(agent_run_id=run.agent_run_id)
            assert result['status'] == ('terminated' if mode == 'limit' else expected)
        if mode == 'complete':
            checkpoint = scope.run_runtime.state_store.load(run.agent_run_id)
            assert checkpoint['final_response'] == result['final_response'].model_dump(mode='json')
        # Intentionally inspect before closing the runtime's SQL session.
        with context.session_factory() as fresh:
            assert fresh.get(AgentRun, run.agent_run_id).status == expected
            assert event in [e.event_type for e in AgentTraceService(fresh).read_trace(agent_run_id=run.agent_run_id)]
        scope.close()
        with context.session_factory() as fresh:
            assert event in [e.event_type for e in AgentTraceService(fresh).read_trace(agent_run_id=run.agent_run_id)]
    finally:
        scope.close()
        product.close()
        context.session_factory.kw['bind'].dispose()


def test_pause_checkpoint_failure_never_commits_paused(freeze_root, monkeypatch):
    context = context_for(freeze_root)
    product = context.create_product()
    workspace = product.create_workspace('Checkpoint failure')
    run = AgentRunService(product.session).create_agent_run(workspace_id=workspace.workspace_id, user_goal='goal', status='running')
    product.session.commit()
    scope = context.runtime_factory.build_run_scope(run.agent_run_id)
    context.runtime_factory.run_control.request_pause(run.agent_run_id)
    original = scope.run_runtime.state_store.save_checkpoint
    writes = []
    def checkpoint(run_id, payload):
        with context.session_factory() as fresh:
            assert fresh.get(AgentRun, run_id).status == 'running'
            assert 'run.paused' not in [e.event_type for e in AgentTraceService(fresh).read_trace(agent_run_id=run_id)]
        if payload['orchestration_state'].get('termination_reason') == 'pause_requested':
            raise OSError('checkpoint unavailable')
        writes.append(run_id)
        return original(run_id, payload)
    monkeypatch.setattr(scope.run_runtime.state_store, 'save_checkpoint', checkpoint)
    try:
        with pytest.raises(OSError, match='checkpoint unavailable'):
            scope.run_runtime.execute(agent_run_id=run.agent_run_id)
        assert writes
        with context.session_factory() as fresh:
            assert fresh.get(AgentRun, run.agent_run_id).status != 'paused'
            assert 'run.paused' not in [e.event_type for e in AgentTraceService(fresh).read_trace(agent_run_id=run.agent_run_id)]
    finally:
        scope.close()
        product.close()
        context.session_factory.kw['bind'].dispose()


def test_crash_after_pause_checkpoint_before_sql_commit_leaves_running(freeze_root, monkeypatch):
    class SimulatedCrash(BaseException):
        pass
    context = context_for(freeze_root)
    product = context.create_product()
    workspace = product.create_workspace('Crash window')
    run = AgentRunService(product.session).create_agent_run(workspace_id=workspace.workspace_id, user_goal='goal', status='running')
    product.session.commit()
    scope = context.runtime_factory.build_run_scope(run.agent_run_id)
    context.runtime_factory.run_control.request_pause(run.agent_run_id)
    def crash():
        raise SimulatedCrash()
    monkeypatch.setattr(scope.run_runtime.state_store, 'commit_boundary', crash)
    try:
        with pytest.raises(SimulatedCrash):
            scope.run_runtime.execute(agent_run_id=run.agent_run_id)
        scope.close()  # process loss rolls back the uncommitted SQL transition
        with context.session_factory() as fresh:
            assert fresh.get(AgentRun, run.agent_run_id).status == 'running'
            assert 'run.paused' not in [e.event_type for e in AgentTraceService(fresh).read_trace(agent_run_id=run.agent_run_id)]
        checkpoint = scope.run_runtime.state_store.load(run.agent_run_id)
        assert checkpoint['orchestration_state']['termination_reason'] == 'pause_requested'
    finally:
        scope.close()
        product.close()
        context.session_factory.kw['bind'].dispose()
