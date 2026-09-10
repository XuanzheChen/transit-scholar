"""Four bounded final freeze gates: intent, admission and input/provider taxonomy."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from transit_scholar.api import create_app
from transit_scholar.db.models import AgentRun, ConversationTurn
from transit_scholar.layer3.planning import RunDecision
from transit_scholar.layer3.run_context import RunRuntimeConfig
from transit_scholar.layer3.trace import AgentTraceService
from transit_scholar.layer2.schema_extraction.errors import LLMRequestError, LLMUnavailableError
from transit_scholar.product.facade import TransitScholarProduct
from transit_scholar.product.runtime import FileRunResearchStateStore
from test_freeze_runtime_integration import context_for, freeze_root
from test_product_core_recovery import prepared, ProcessLoss


@pytest.mark.parametrize('mode', ['completed', 'cancelled', 'terminated'])
def test_terminal_intent_survives_precommit_crash(freeze_root, monkeypatch, mode):
    context = context_for(freeze_root, coordinator=lambda _: RunDecision(mode='complete', completion_reason='answer A'))
    product, _, _, message = prepared(context)
    product.research.execution.update_agent_run_status(message.agent_run_id, 'running')
    product.session.commit()
    scope = context.runtime_factory.build_run_scope(message.agent_run_id)
    runtime = scope.run_runtime
    if mode == 'cancelled':
        runtime.is_cancelled = lambda: True
    if mode == 'terminated':
        runtime.config = runtime.config.model_copy(update={'max_run_steps': 0})
    def crash():
        raise ProcessLoss()
    monkeypatch.setattr(runtime, '_commit_boundary', crash)
    with pytest.raises(ProcessLoss):
        runtime.execute(agent_run_id=message.agent_run_id)
    original = runtime.state_store.load(message.agent_run_id)
    assert original['orchestration_state']['status'] == mode
    scope.close()
    product.close()
    with context.session_factory() as fresh:
        assert fresh.get(AgentRun, message.agent_run_id).status == 'running'
    original_build = context.runtime_factory.build_run_scope
    counts = []
    def build(run_id):
        result = original_build(run_id)
        def forbidden(*args, **kwargs):
            counts.append('research rerun')
            raise AssertionError('terminal intent must not rerun research')
        result.run_runtime.coordinator = forbidden
        result.run_runtime.synthesis = forbidden
        result.run_runtime._recover_current = forbidden
        return result
    monkeypatch.setattr(context.runtime_factory, 'build_run_scope', build)
    app = create_app(runtime_context=context)
    done = Event()
    release = app.state.execution_manager._release
    def released(*args, **kwargs):
        release(*args, **kwargs)
        done.set()
    monkeypatch.setattr(app.state.execution_manager, '_release', released)
    try:
        with TestClient(app) as client:
            assert client.get(f'/api/v1/runs/{message.agent_run_id}').json()['status'] == 'paused'
            assert client.post(f'/api/v1/runs/{message.agent_run_id}/resume').status_code == 202
            assert done.wait(20)
            with context.session_factory() as fresh:
                expected = 'failed' if mode == 'terminated' else mode
                assert fresh.get(AgentRun, message.agent_run_id).status == expected
                turn = fresh.get(ConversationTurn, message.turn_id)
                assert turn.status == ('completed' if mode == 'completed' else 'failed')
                if mode == 'completed':
                    assert turn.final_assistant_response == original['final_response']
                events = [e.event_type for e in AgentTraceService(fresh).read_trace(agent_run_id=message.agent_run_id)]
                assert events.count('run.completed' if mode == 'completed' else 'run.failed') == 1
            assert FileRunResearchStateStore(context.runtime_factory.runtime_root).load(message.agent_run_id) == original
            assert counts == []
    finally:
        context.session_factory.kw['bind'].dispose()


@pytest.mark.parametrize('operation', ['schema', 'wiki'])
@pytest.mark.parametrize('fails', [False, True])
def test_mutation_first_blocks_prompt_admission(freeze_root, monkeypatch, operation, fails):
    context = context_for(freeze_root, coordinator=lambda _: RunDecision(mode='complete', completion_reason='done'))
    product = context.create_product()
    workspace = product.create_workspace('exclusive')
    conversation = product.create_conversation(workspace.workspace_id, 'exclusive')
    product.session.commit()
    product.close()
    entered, finish = Event(), Event()
    method = 'materialize_workspace_schema' if operation == 'schema' else 'build_workspace_wiki'
    def blocked(self, workspace_id, *args):
        self._guard_workspace_mutation(workspace_id)
        entered.set()
        assert finish.wait(20)
        if fails:
            from transit_scholar.layer3.workspace.errors import WorkspaceNotFoundError
            raise WorkspaceNotFoundError('fixture operation ended')
        return SimpleNamespace(run_id='schema-result', run_manifest=SimpleNamespace(status='completed'),
                               fingerprint='fixture', provenance=SimpleNamespace(build_revision=1))
    monkeypatch.setattr(TransitScholarProduct, method, blocked)
    app = create_app(runtime_context=context)
    suffix = '/papers/p/schema/materialize' if operation == 'schema' else '/wiki/build'
    try:
        with TestClient(app) as client, ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(client.post, f'/api/v1/workspaces/{workspace.workspace_id}{suffix}')
            try:
                assert entered.wait(10)
                response = client.post(f'/api/v1/conversations/{conversation.id}/turns', json={'message': 'first'})
                assert response.status_code == 409
                assert response.json()['error']['code'] == 'RUNNER_BUSY'
                with context.session_factory() as fresh:
                    assert fresh.scalars(select(AgentRun)).all() == []
                    assert fresh.scalars(select(ConversationTurn)).all() == []
            finally:
                finish.set()
            assert pending.result().status_code == (404 if fails else 200)
            assert client.post(f'/api/v1/conversations/{conversation.id}/turns', json={'message': 'later'}).status_code == 202
    finally:
        context.session_factory.kw['bind'].dispose()


@pytest.mark.parametrize('field', ['schema_id', 'version'])
def test_unsafe_schema_identity_is_422(freeze_root, field):
    context = context_for(freeze_root)
    identity = {'schema_id': 'safe', 'version': '1.0', field: '_invalid'}
    try:
        with TestClient(create_app(runtime_context=context)) as client:
            responses = [
                client.post('/api/v1/workspaces', json={'name': 'invalid', 'schema': identity}),
                client.post('/api/v1/schemas', json={**identity, 'sections': []}),
                client.get(f"/api/v1/schemas/{identity['schema_id']}/versions/{identity['version']}"),
            ]
            for response in responses:
                assert response.status_code == 422
                assert set(response.json()) == {'error'}
                assert 'traceback' not in response.text.lower()
            assert responses[-1].json()['error'] == {'code': 'SCHEMA_INVALID', 'message': 'Schema definition is invalid', 'details': {}}
    finally:
        context.session_factory.kw['bind'].dispose()


@pytest.mark.parametrize('error', [LLMRequestError('secret timeout'), LLMRequestError('secret rate limit', status_code=429), LLMUnavailableError('secret provider')])
def test_goal_provider_failure_releases_admission(freeze_root, error):
    context = context_for(freeze_root)
    def fail(*args, **kwargs):
        raise error
    context._llm_client = SimpleNamespace(generate_structured=fail)
    product = context.create_product()
    workspace = product.create_workspace('followup')
    conversation = product.create_conversation(workspace.workspace_id, 'followup')
    product.conversations.create_turn(conversation.id, 'prior', status='completed')
    product.session.commit()
    product.close()
    app = create_app(runtime_context=context)
    try:
        with TestClient(app) as client:
            response = client.post(f'/api/v1/conversations/{conversation.id}/turns', json={'message': 'follow up'})
            assert response.status_code == 503
            assert response.json()['error']['code'] == 'PROVIDER_UNAVAILABLE'
            assert 'secret' not in response.text
            reservation = app.state.execution_manager.reserve()
            reservation.release()
            with context.session_factory() as fresh:
                assert fresh.scalars(select(AgentRun)).all() == []
                turns = fresh.scalars(select(ConversationTurn).order_by(ConversationTurn.sequence)).all()
                assert [t.status for t in turns] == ['completed', 'failed']
    finally:
        context.session_factory.kw['bind'].dispose()
