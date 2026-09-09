from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi.testclient import TestClient

from transit_scholar.api import create_app
from test_conversations_api import AvailableContext, UnavailableContext
from transit_scholar.api.dependencies import get_product
from transit_scholar.db.models import AgentRun
from transit_scholar.layer3.execution import AgentRunService
from transit_scholar.layer3.trace import AgentTraceService
from transit_scholar.product.facade import TransitScholarProduct
from transit_scholar.product.projection import ProductStateProjector


class Control:
    def __init__(self):
        self.requested = set()

    def request_pause(self, run_id):
        self.requested.add(run_id)

    def clear_pause(self, run_id):
        self.requested.discard(run_id)

    def is_pause_requested(self, run_id):
        return run_id in self.requested


class Manager:
    is_busy = False

    def __init__(self):
        self.submissions = []

    def submit(self, run_id, **kwargs):
        self.submissions.append((run_id, kwargs))

    def shutdown(self, wait=True):
        pass


def test_timeline_is_ordered_incremental_and_private_fields_are_omitted(session, project_tmp_path):
    product = TransitScholarProduct(session, runtime_factory=None, data_root=project_tmp_path)
    workspace = product.create_workspace("timeline")
    run = AgentRunService(session).create_agent_run(workspace_id=workspace.workspace_id, user_goal="goal")
    trace = AgentTraceService(session)
    trace.append_event(agent_run_id=run.agent_run_id, event_type="run.plan.created", payload={"message": "plan", "prompt": "secret"})
    trace.append_event(agent_run_id=run.agent_run_id, event_type="query.created", payload={"query_id": "q1", "scratchpad": "secret"})
    trace.append_event(agent_run_id=run.agent_run_id, event_type="evidence.admitted", payload={"evidence_id": "e1", "provider_reasoning": "secret"})
    trace.append_event(
        agent_run_id=run.agent_run_id,
        event_type="runtime.failure",
        payload={
            "failure_message": "ProviderError: sk-test-secret raw model reasoning",
            "provider_output": "private completion",
        },
    )
    session.commit()
    app = create_app(data_root=project_tmp_path)
    app.dependency_overrides[get_product] = lambda: product
    with TestClient(app) as client:
        first = client.get(f"/api/v1/runs/{run.agent_run_id}/timeline")
        assert first.status_code == 200
        body = first.json()
        assert [event["sequence"] for event in body["events"]] == [1, 2, 3, 4]
        assert body["next_sequence"] == 4
        serialized = str(body)
        assert "secret" not in serialized
        assert "raw model reasoning" not in serialized
        assert "private completion" not in serialized
        assert body["events"][-1]["data"] == {
            "code": "PROVIDER_REQUEST_FAILED",
            "summary": "The provider request could not be completed.",
        }
        second = client.get(f"/api/v1/runs/{run.agent_run_id}/timeline?after_sequence=1")
        assert [event["sequence"] for event in second.json()["events"]] == [2, 3, 4]
        assert second.json()["next_sequence"] == 4


def test_pause_and_resume_endpoints(session, project_tmp_path):
    control = Control()
    runtime_factory = SimpleNamespace(run_control=control)
    product = TransitScholarProduct(session, runtime_factory=runtime_factory, data_root=project_tmp_path)
    workspace = product.create_workspace("controls")
    run = AgentRunService(session).create_agent_run(workspace_id=workspace.workspace_id, user_goal="goal", status="running")
    manager = Manager()
    app = create_app(data_root=project_tmp_path, runtime_context=AvailableContext(project_tmp_path), execution_manager=manager)
    app.dependency_overrides[get_product] = lambda: product
    with TestClient(app) as client:
        paused = client.post(f"/api/v1/runs/{run.agent_run_id}/pause")
        assert paused.status_code == 200
        assert paused.json()["pause_requested"] is True
        session.get(AgentRun, run.agent_run_id).status = "paused"
        session.flush()
        resumed = client.post(f"/api/v1/runs/{run.agent_run_id}/resume")
        assert resumed.status_code == 202
        assert manager.submissions == [(run.agent_run_id, {"resume": True})]

def test_unavailable_resume_preserves_paused_run_and_checkpoint(session, project_tmp_path):
    from transit_scholar.product.runtime import FileRunResearchStateStore
    manager = Manager()
    manager.reserve_calls = 0
    def reserve():
        manager.reserve_calls += 1
        raise AssertionError('unavailable runtime must not reserve')
    manager.reserve = reserve
    product = TransitScholarProduct(session, runtime_factory=None, data_root=project_tmp_path)
    workspace = product.create_workspace('unavailable')
    run = AgentRunService(session).create_agent_run(workspace_id=workspace.workspace_id, user_goal='goal', status='paused')
    session.commit()
    store = FileRunResearchStateStore(project_tmp_path)
    checkpoint = {'status': 'paused', 'current_role_execution_id': 'role', 'next_action_index': 1}
    store.save(run.agent_run_id, checkpoint)
    app = create_app(runtime_context=UnavailableContext(project_tmp_path), execution_manager=manager)
    app.dependency_overrides[get_product] = lambda: product
    with TestClient(app) as client:
        response = client.post(f'/api/v1/runs/{run.agent_run_id}/resume')
        assert response.status_code == 503
        assert response.json()['error']['code'] == 'PROVIDER_UNAVAILABLE'
        assert manager.reserve_calls == 0
        assert manager.submissions == []
        session.expire_all()
        assert session.get(AgentRun, run.agent_run_id).status == 'paused'
        assert store.load(run.agent_run_id) == checkpoint
