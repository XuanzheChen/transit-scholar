from fastapi.testclient import TestClient

from transit_scholar.api import create_app
from transit_scholar.api.schemas.papers import PaperSummaryResponse


def test_openapi_exposes_every_contract_endpoint(project_tmp_path):
    app = create_app(data_root=project_tmp_path / "api")
    paths = app.openapi()["paths"]
    expected_operations = {
        "get": {
            "/api/v1/health", "/api/v1/capabilities", "/api/v1/papers",
            "/api/v1/papers/{paper_id}", "/api/v1/papers/{paper_id}/files",
            "/api/v1/files/{file_id}/content", "/api/v1/papers/{paper_id}/citations",
            "/api/v1/papers/{paper_id}/metadata-candidates", "/api/v1/papers/{paper_id}/enrichment",
            "/api/v1/papers/{paper_id}/duplicate-relations", "/api/v1/schemas",
            "/api/v1/schemas/{schema_id}/versions/{version}", "/api/v1/workspaces",
            "/api/v1/workspaces/{workspace_id}", "/api/v1/workspaces/{workspace_id}/papers",
            "/api/v1/workspaces/{workspace_id}/schema",
            "/api/v1/workspaces/{workspace_id}/papers/{paper_id}/schema",
            "/api/v1/workspaces/{workspace_id}/conversations", "/api/v1/conversations/{conversation_id}",
            "/api/v1/turns/{turn_id}", "/api/v1/runs/{agent_run_id}",
            "/api/v1/runs/{agent_run_id}/timeline", "/api/v1/workspaces/{workspace_id}/wiki",
            "/api/v1/workspaces/{workspace_id}/wiki/status", "/api/v1/workspaces/{workspace_id}/wiki/pages",
            "/api/v1/workspaces/{workspace_id}/wiki/pages/{page_id}",
            "/api/v1/workspaces/{workspace_id}/wiki/entities",
            "/api/v1/workspaces/{workspace_id}/wiki/entities/{entity_id}",
            "/api/v1/workspaces/{workspace_id}/wiki/agentic-entries",
            "/api/v1/workspaces/{workspace_id}/wiki/agentic-entries/{entry_id}",
            "/api/v1/workspaces/{workspace_id}/wiki/search",
        },
        "post": {
            "/api/v1/papers/import", "/api/v1/papers/{paper_id}/restore",
            "/api/v1/papers/{paper_id}/reconcile", "/api/v1/papers/{paper_id}/enrichment/refresh",
            "/api/v1/duplicate-relations/{relation_id}/resolve", "/api/v1/schemas/validate",
            "/api/v1/schemas", "/api/v1/workspaces", "/api/v1/workspaces/{workspace_id}/archive",
            "/api/v1/workspaces/{workspace_id}/papers",
            "/api/v1/workspaces/{workspace_id}/papers/{paper_id}/schema/materialize",
            "/api/v1/workspaces/{workspace_id}/conversations", "/api/v1/conversations/{conversation_id}/turns",
            "/api/v1/runs/{agent_run_id}/pause", "/api/v1/runs/{agent_run_id}/resume",
            "/api/v1/workspaces/{workspace_id}/wiki/build",
        },
        "patch": {"/api/v1/papers/{paper_id}/metadata"},
        "delete": {
            "/api/v1/papers/{paper_id}", "/api/v1/workspaces/{workspace_id}",
            "/api/v1/workspaces/{workspace_id}/papers/{paper_id}",
        },
    }
    for method, endpoint_paths in expected_operations.items():
        assert all(method in paths[path] for path in endpoint_paths)


def test_system_endpoints_have_explicit_response_models(project_tmp_path):
    app = create_app(data_root=project_tmp_path / "api")
    paths = app.openapi()["paths"]
    assert paths["/api/v1/health"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith("HealthResponse")
    assert paths["/api/v1/capabilities"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith("CapabilityResponse")


def test_validation_and_not_found_errors_use_the_stable_envelope(project_tmp_path):
    with TestClient(create_app(data_root=project_tmp_path / "api")) as client:
        invalid = client.post("/api/v1/workspaces", json={})
        missing = client.get("/api/v1/schemas/missing/versions/1.0")

    for response, status_code, code in (
        (invalid, 422, "VALIDATION_ERROR"),
        (missing, 404, "NOT_FOUND"),
    ):
        assert response.status_code == status_code
        assert set(response.json()["error"]) == {"code", "message", "details"}
        assert response.json()["error"]["code"] == code


def test_response_dto_omits_internal_model_fields():
    assert "internal_runtime_checkpoint" not in PaperSummaryResponse.model_fields
    assert "internal_runtime_checkpoint" not in PaperSummaryResponse.model_json_schema()["properties"]


def test_capabilities_do_not_advertise_agent_execution_without_runtime(project_tmp_path):
    with TestClient(create_app(data_root=project_tmp_path / "api")) as client:
        capabilities = client.get("/api/v1/capabilities").json()

    assert capabilities["pause_resume"] is False
    assert capabilities["user_schema_creation"] is True


def test_capabilities_advertise_agent_execution_with_configured_runtime(project_tmp_path, monkeypatch):
    monkeypatch.setenv("TRANSIT_SCHOLAR_LLM_PROVIDER", "fake")
    with TestClient(create_app(data_root=project_tmp_path / "api")) as client:
        capabilities = client.get("/api/v1/capabilities").json()

    assert capabilities["pause_resume"] is True

def test_injected_settings_drive_capabilities_and_upload_limit(project_tmp_path):
    from transit_scholar.api.runtime_context import ApiRuntimeContext
    from transit_scholar.config import Settings
    context = ApiRuntimeContext(Settings(data_root=project_tmp_path, max_file_size_bytes=4))
    with TestClient(create_app(runtime_context=context)) as client:
        assert client.get('/api/v1/capabilities').json()['pdf_upload_max_bytes'] == 4
        response = client.post('/api/v1/papers/import', files={'file': ('large.pdf', b'%PDF-too-large', 'application/pdf')})
        assert response.status_code == 413
