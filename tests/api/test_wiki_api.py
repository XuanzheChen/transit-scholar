from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi.testclient import TestClient

from transit_scholar.api import create_app
from transit_scholar.api.dependencies import get_product
from transit_scholar.api.routers import wiki
from transit_scholar.product.facade import TransitScholarProduct


def _client(session, project_tmp_path):
    product = TransitScholarProduct(session, runtime_factory=None, data_root=project_tmp_path)
    app = create_app(data_root=project_tmp_path)
    app.include_router(wiki.router)
    app.dependency_overrides[get_product] = lambda: product
    return TestClient(app), product


def _draft():
    return {
        "schema_id": "wiki_api_schema", "version": "1.0",
        "sections": [{"id": "overview", "label": "Overview", "fields": [{
            "id": "summary", "label": "Summary", "question": "Summarize", "type": "string",
        }]}],
    }


def test_no_schema_workspace_reports_base_wiki_unsupported(session, project_tmp_path):
    client, product = _client(session, project_tmp_path)
    workspace = product.create_workspace("No Schema")

    for path in ("wiki", "wiki/status"):
        response = client.get(f"/api/v1/workspaces/{workspace.workspace_id}/{path}")
        assert response.status_code == 200
        body = response.json()
        status = body["base_wiki"]["status"] if path == "wiki" else body["status"]
        assert status == "unsupported"


def test_wiki_api_delegates_build_and_returns_structured_resources(session, project_tmp_path, monkeypatch):
    client, product = _client(session, project_tmp_path)
    product.schema_catalog.create(_draft())
    workspace = product.create_workspace("Bound", "wiki_api_schema", "1.0")
    workspace_id = workspace.workspace_id
    now = datetime.now(timezone.utc)
    wiki_status = SimpleNamespace(
        workspace_id=workspace_id, status="ready", manifest_status="complete",
        fingerprint="fingerprint", recorded_fingerprint="fingerprint", build_revision=2,
        built_at=now, error_code=None,
        model_dump=lambda: {
            "workspace_id": workspace_id, "status": "ready", "manifest_status": "complete",
            "fingerprint": "fingerprint", "recorded_fingerprint": "fingerprint",
            "build_revision": 2, "built_at": now, "error_code": None,
        },
    )
    monkeypatch.setattr(product, "workspace_wiki_status", lambda _: wiki_status)
    monkeypatch.setattr(product, "build_workspace_wiki", lambda _: SimpleNamespace(
        fingerprint="fingerprint", provenance=SimpleNamespace(build_revision=2)
    ))
    monkeypatch.setattr(product, "list_workspace_wiki_pages", lambda _: [SimpleNamespace(model_dump=lambda: {
        "page_id": "page-1", "workspace_id": workspace_id, "paper_id": "paper-1",
        "title": "Structured Page", "summary": "JSON data", "schema_id": "schema",
        "schema_version": "1", "build_status": "complete", "created_at": now,
        "updated_at": now, "build_revision": 1,
    })])
    monkeypatch.setattr(product, "list_workspace_wiki_entities", lambda _: [SimpleNamespace(model_dump=lambda: {
        "entity_id": "entity-1", "workspace_id": workspace_id, "canonical_name": "Transit",
        "aliases": [], "description": "A topic", "kind": "concept", "created_at": now, "updated_at": now,
    })])

    assert client.post(f"/api/v1/workspaces/{workspace_id}/wiki/build").json()["fingerprint"] == "fingerprint"
    assert client.get(f"/api/v1/workspaces/{workspace_id}/wiki/pages").json()["items"][0]["summary"] == "JSON data"
    assert client.get(f"/api/v1/workspaces/{workspace_id}/wiki/entities").json()["items"][0]["canonical_name"] == "Transit"


def test_wiki_search_preserves_base_and_agentic_source_kinds(session, project_tmp_path, monkeypatch):
    client, product = _client(session, project_tmp_path)
    workspace = product.create_workspace("Search")
    monkeypatch.setattr(product, "search_workspace_wiki", lambda *args, **kwargs: SimpleNamespace(model_dump=lambda: {
        "status": "ok", "error_code": None,
        "source_status": {"base_wiki": "ok", "agentic_wiki": "ok"},
        "source_errors": {"base_wiki": None, "agentic_wiki": None},
        "hits": [
            {"type": "page", "object_id": "page-1", "title": "Base", "score": 0.9,
             "snippet": "base result", "retrieval_mode": "lexical", "source_kind": "base_wiki",
             "lifecycle_status": None, "source_score": 0.9, "local_rank": 1, "fusion_score": 0.9},
            {"type": "entry", "object_id": "entry-1", "title": "Agentic", "score": 0.8,
             "snippet": "agentic result", "retrieval_mode": "lexical", "source_kind": "agentic_wiki",
             "lifecycle_status": "active", "source_score": 0.8, "local_rank": 1, "fusion_score": 0.8},
        ],
    }))

    response = client.get(f"/api/v1/workspaces/{workspace.workspace_id}/wiki/search", params={"query": "transit"})
    assert response.status_code == 200
    assert {hit["source_kind"] for hit in response.json()["hits"]} == {"base_wiki", "agentic_wiki"}
