from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from transit_scholar.layer3.agent import RoleId, RoleRegistry, RoleRuntimeProfile, built_in_role_registry
from transit_scholar.layer3.context import RoleContext
from transit_scholar.layer3.runtime import FileRoleExecutionStore, RoleRuntime, MainResearchRuntime

class Output(BaseModel):
    completed: bool = True
    next_role_id: str | None = None
    actions: list[dict[str, str]]

class StateStore:
    payload = None
    def load_research_state(self, **kwargs):
        return SimpleNamespace(payload=self.payload) if self.payload else None
    def save_research_state(self, *, payload, **kwargs):
        self.payload = payload

@pytest.mark.parametrize('boundary', ['decision_validated', 'action_committed'])
@pytest.mark.parametrize('through_main', [False, True])
def test_default_budget_pause_continuation(project_tmp_path, boundary, through_main):
    role = built_in_role_registry({'research_coordinator': RoleRuntimeProfile(max_steps=1, max_llm_calls=1, max_tool_calls=2)}).get('research_coordinator').model_copy(update={'output_contract': Output})
    registry = RoleRegistry([role])
    store = FileRoleExecutionStore(project_tmp_path / 'roles')
    requested = [False]
    committed = []
    policy_calls = []
    class Policy:
        def decide(self, *args):
            policy_calls.append(True)
            return Output(actions=[{'id': 'A'}, {'id': 'B'}])
    class Executor:
        def execute(self, action, role):
            committed.append(action['id'])
            return {'committed': action['id']}
    class Trace:
        def append_event(self, **kwargs):
            if kwargs['payload'].get('classification') == boundary:
                requested[0] = True
    def runtime(trace=None):
        return RoleRuntime(registry, store, action_executor=Executor(), trace=trace, is_pause_requested=lambda: requested[0])
    context = RoleContext(role_id=role.role_id.value, sections={}, omitted_sections=frozenset(), serialized_chars=2)
    role_input = {'research_session_id': 'session', 'research_goal': 'goal'}
    state_store = StateStore()
    def main(rr):
        return MainResearchRuntime(registry=registry, role_runtime=rr,
            execution_service=SimpleNamespace(get_agent_run=lambda _: {}, get_research_session=lambda *_: {}, update_research_session_status=lambda *args: None),
            context_builder=SimpleNamespace(build=lambda **kwargs: None), projector=SimpleNamespace(project=lambda *args: context),
            role_input_factory=lambda *args: role_input, policies={RoleId.RESEARCH_COORDINATOR: Policy()}, state_store=state_store)
    kwargs = dict(agent_run_id='run', research_session_id='session')
    if through_main:
        paused = main(runtime(Trace())).execute(**kwargs)
        execution_id = state_store.payload['l3s5']['current_role_execution_id']
        assert paused.usage.model_dump() == dict(steps=0, llm_calls=0, tool_calls=0, failures=0)
        assert paused.role_results == []
    else:
        paused = runtime(Trace()).execute(role, role_input, Policy(), role_context=context, role_execution_id='role', **kwargs)
        execution_id = paused.role_execution_id
    assert paused.status == 'paused'
    persisted = store.load(execution_id)
    assert persisted.working_state.last_output is not None
    assert persisted.working_state.current_step == 1
    assert persisted.working_state.usage.llm_calls == len(policy_calls) == 1
    assert persisted.working_state.next_action_index == (1 if boundary == 'action_committed' else 0)
    if boundary == 'action_committed':
        assert persisted.working_state.intermediate_artifacts[0]['result'] == {'committed': 'A'}
    requested[0] = False
    if through_main:
        result = main(runtime()).resume_session(**kwargs)
        assert result.usage.steps == 1
        assert result.usage.llm_calls == len(policy_calls) == 1
        assert result.usage.tool_calls == len(committed) == 2
        assert len(result.role_results) == 1
        assert result.role_results[0].role_execution_id == execution_id
    else:
        result = runtime().execute(role, role_input, Policy(), role_context=context, role_execution_id=execution_id, **kwargs)
        assert result.role_execution_id == execution_id
        assert result.working_state.usage.tool_calls == 2
    assert result.status == 'completed'
    assert result.termination_reason == 'semantic_completion'
    assert len(policy_calls) == 1
    assert committed == ['A', 'B']
