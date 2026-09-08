from types import SimpleNamespace

from fastapi.testclient import TestClient

from transit_scholar.api import create_app
from transit_scholar.api.dependencies import get_product
from transit_scholar.db.models import AgentRun, Paper
from transit_scholar.product.facade import TransitScholarProduct


def _draft():
    return {
        "schema_id": "workspace_api_schema",
        "version": "1.0",
        "sections": [{
            "id": "overview",
            "label": "Overview",
            "fields": [{
                "id": "summary",
                "label": "Summary",
                "question": "What is the summary?",
                "type": "string",
            }],
        }],
    }


def _client(session, project_tmp_path):
    product = TransitScholarProduct(session, runtime_factory=None, data_root=project_tmp_path)
    app = create_app(data_root=project_tmp_path)
    app.dependency_overrides[get_product] = lambda: product
    return TestClient(app), product


def test_workspace_api_preserves_schema_binding_and_membership(session, project_tmp_path):
    client, product = _client(session, project_tmp_path)
    session.add(Paper(id="paper-api", title="Global paper"))
    session.flush()

    no_schema = client.post("/api/v1/workspaces", json={"name": "No schema"})
    assert no_schema.status_code == 201
    assert no_schema.json()["schema_mode"] == "none"
    assert no_schema.json()["schema_binding"] is None

    assert client.post("/api/v1/schemas", json=_draft()).status_code == 201
    created = client.post("/api/v1/workspaces", json={
        "name": "Bound", "schema": {"schema_id": "workspace_api_schema", "version": "1.0"},
    })
    assert created.status_code == 201
    workspace = created.json()
    workspace_id = workspace["workspace_id"]
    assert workspace["schema_binding"]["schema_id"] == "workspace_api_schema"
    assert workspace["schema_binding"]["schema_version"] == "1.0"

    paths = client.get("/openapi.json").json()["paths"]
    assert not any("schema/rebind" in path for path in paths)
    assert client.get(f"/api/v1/workspaces/{workspace_id}/schema").json()["binding"] == workspace["schema_binding"]

    added = client.post(f"/api/v1/workspaces/{workspace_id}/papers", json={"paper_id": "paper-api"})
    assert added.status_code == 200
    assert client.get(f"/api/v1/workspaces/{workspace_id}/papers").json()["items"][0]["paper_id"] == "paper-api"
    assert client.delete(f"/api/v1/workspaces/{workspace_id}/papers/paper-api").status_code == 200
    assert session.get(Paper, "paper-api").status == "active"


def test_workspace_api_blocks_mutation_and_dispatches_materialization(session, project_tmp_path, monkeypatch):
    client, product = _client(session, project_tmp_path)
    session.add(Paper(id="paper-busy", title="Global paper"))
    session.flush()
    product.schema_catalog.create(_draft())
    workspace = product.create_workspace("Bound", "workspace_api_schema", "1.0")
    product.add_workspace_paper(workspace.workspace_id, "paper-busy")
    session.add(AgentRun(
        id="run-busy", workspace_id=workspace.workspace_id, workspace_revision=workspace.revision,
        user_goal="Keep workspace fixed", status="paused",
    ))
    session.flush()

    for response in (
        client.post(f"/api/v1/workspaces/{workspace.workspace_id}/papers", json={"paper_id": "paper-busy"}),
        client.delete(f"/api/v1/workspaces/{workspace.workspace_id}/papers/paper-busy"),
        client.post(f"/api/v1/workspaces/{workspace.workspace_id}/archive"),
        client.delete(f"/api/v1/workspaces/{workspace.workspace_id}"),
    ):
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "WORKSPACE_BUSY"

    session.get(AgentRun, "run-busy").status = "completed"
    session.flush()
    called = {}

    def materialize(workspace_id, paper_id):
        called.update(workspace_id=workspace_id, paper_id=paper_id)
        return SimpleNamespace(run_id="schema-run", status="completed")

    monkeypatch.setattr(product, "materialize_workspace_schema", materialize)
    response = client.post(
        f"/api/v1/workspaces/{workspace.workspace_id}/papers/paper-busy/schema/materialize"
    )
    assert response.status_code == 200
    assert response.json()["run_id"] == "schema-run"
    assert called == {"workspace_id": workspace.workspace_id, "paper_id": "paper-busy"}
