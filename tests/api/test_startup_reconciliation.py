from fastapi.testclient import TestClient

from transit_scholar.api import create_app
from transit_scholar.api.dependencies import get_product
from transit_scholar.db.models import AgentRun
from transit_scholar.layer3.execution import AgentRunService
from transit_scholar.product.facade import TransitScholarProduct


class RecordingManager:
    is_busy = False

    def __init__(self, product):
        self.product = product
        self.submissions = []

    def submit(self, run_id, **kwargs):
        self.submissions.append((run_id, kwargs))

    def reconcile_interrupted_runs(self):
        return self.product.reconcile_interrupted_runs(set())

    def shutdown(self, wait=True):
        pass


def test_startup_pauses_interrupted_run_without_execution_and_allows_resume(
    session, project_tmp_path, monkeypatch
):
    product = TransitScholarProduct(session, runtime_factory=None, data_root=project_tmp_path)
    workspace = product.create_workspace("interrupted run")
    run = AgentRunService(session).create_agent_run(
        workspace_id=workspace.workspace_id,
        user_goal="Resume this later",
        status="running",
    )
    session.commit()

    def execution_must_not_start(*args, **kwargs):
        raise AssertionError("startup must not execute or resume an AgentRun")

    monkeypatch.setattr(product, "execute_run", execution_must_not_start)
    monkeypatch.setattr(product, "resume_run", execution_must_not_start)

    app = create_app(data_root=project_tmp_path)
    app.state.product_factory = lambda: product
    manager = RecordingManager(product)
    app.state.execution_manager = manager
    app.dependency_overrides[get_product] = lambda: product

    with TestClient(app) as client:
        app.state.runtime_context.runtime_factory = object()
        session.expire_all()
        assert session.get(AgentRun, run.agent_run_id).status == "paused"

        response = client.post(f"/api/v1/runs/{run.agent_run_id}/resume")

    assert response.status_code == 202
    assert manager.submissions == [(run.agent_run_id, {"resume": True})]
