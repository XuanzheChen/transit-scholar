from fastapi.testclient import TestClient

from transit_scholar.api import create_app


def _draft(schema_id="api_user_schema", version="1.0"):
    return {
        "schema_id": schema_id,
        "version": version,
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


def test_schema_api_unifies_catalog_validates_without_persisting_and_rejects_duplicates(tmp_path):
    client = TestClient(create_app(data_root=tmp_path / "api"))

    invalid = client.post("/api/v1/schemas/validate", json={**_draft(), "sections": []})
    assert invalid.status_code == 200
    assert invalid.json() == {"valid": False, "issues": invalid.json()["issues"]}
    assert invalid.json()["issues"]

    created = client.post("/api/v1/schemas", json=_draft())
    assert created.status_code == 201
    assert created.json()["schema_id"] == "api_user_schema"

    catalog = client.get("/api/v1/schemas")
    pairs = {(item["schema_id"], item["version"]) for item in catalog.json()}
    assert ("api_user_schema", "1.0") in pairs
    assert ("bus_control_rl", "1.0") in pairs

    duplicate = client.post("/api/v1/schemas", json=_draft())
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "SCHEMA_VERSION_EXISTS"
