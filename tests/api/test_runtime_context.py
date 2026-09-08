from fastapi.testclient import TestClient

from transit_scholar.api import create_app
from transit_scholar.api.runtime import LocalExecutionManager


def test_http_requests_reuse_bootstrap_and_receive_distinct_sessions(project_tmp_path, monkeypatch):
    import transit_scholar.product.bootstrap as bootstrap

    original_bootstrap = bootstrap.build_local_product
    bootstrap_calls = []

    def recording_bootstrap(settings):
        bootstrap_calls.append(settings)
        return original_bootstrap(settings)

    monkeypatch.setattr(bootstrap, "build_local_product", recording_bootstrap)
    app = create_app(data_root=project_tmp_path)
    created_sessions = []
    original_create_product = app.state.runtime_context.create_product

    def recording_product():
        product = original_create_product()
        created_sessions.append(product.session)
        return product

    app.state.runtime_context.create_product = recording_product
    with TestClient(app) as client:
        assert client.get("/api/v1/schemas").status_code == 200
        assert client.get("/api/v1/schemas").status_code == 200

    assert len(bootstrap_calls) == 1
    assert len(created_sessions) == 2
    assert created_sessions[0] is not created_sessions[1]


def test_worker_scope_remains_valid_after_request_scope_closes(project_tmp_path):
    class Session:
        closed = False

        def close(self):
            self.closed = True

    class Product:
        def __init__(self, session):
            self.session = session

        def execute_run(self, _run_id):
            assert not self.session.closed
            return "executed"

        def close(self):
            self.session.close()

    request_session = Session()
    request_product = Product(request_session)
    request_product.close()
    worker_sessions = []

    def worker_factory():
        session = Session()
        worker_sessions.append(session)
        return Product(session)

    manager = LocalExecutionManager(worker_factory)
    try:
        assert manager.submit("run-1").result(timeout=2) == "executed"
    finally:
        manager.shutdown()

    assert request_session.closed
    assert len(worker_sessions) == 1
    assert worker_sessions[0] is not request_session
    assert worker_sessions[0].closed
    assert not manager.is_busy
    assert manager.future("run-1") is None
