"""Static hosting contract for the built local Web UI.

The formal product UI must be reachable from the same FastAPI origin as
``/api/v1/health`` while the Stage 7 acceptance panel stays a separate app.
"""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from transit_scholar.api.app import (
    FRONTEND_BUILD_MISSING,
    create_app,
    resolve_ui_dist_dir,
)

SPA_MARKER = "TRANSITSCHOLAR_FRONTEND_BUILD"


def _write_fake_build(root: Path) -> Path:
    """Create a minimal Vite-shaped build directory."""
    dist = root / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(
        f'<!doctype html><html><body><div id="root">{SPA_MARKER}</div></body></html>',
        encoding="utf-8",
    )
    (dist / "assets" / "index-abc123.js").write_text("console.log('ui')", encoding="utf-8")
    (dist / "assets" / "index-abc123.css").write_text("body{color:#23221f}", encoding="utf-8")
    (dist / "favicon.svg").write_text("<svg/>", encoding="utf-8")
    return dist


def _client(project_tmp_path, dist: Path) -> TestClient:
    return TestClient(create_app(data_root=project_tmp_path / "api", ui_dist=dist))


def test_built_frontend_and_api_share_one_origin(project_tmp_path):
    dist = _write_fake_build(project_tmp_path)
    with _client(project_tmp_path, dist) as client:
        index = client.get("/")
        script = client.get("/assets/index-abc123.js")
        stylesheet = client.get("/assets/index-abc123.css")
        favicon = client.get("/favicon.svg")
        health = client.get("/api/v1/health")
        capabilities = client.get("/api/v1/capabilities")

    assert index.status_code == 200
    assert index.headers["content-type"].startswith("text/html")
    assert SPA_MARKER in index.text
    assert script.status_code == 200 and "console.log" in script.text
    # Windows' registry-derived mimetypes database can report .js as text/plain,
    # which makes browsers refuse to execute the frontend module bundle.
    assert script.headers["content-type"].startswith("text/javascript")
    assert stylesheet.status_code == 200 and "color" in stylesheet.text
    assert stylesheet.headers["content-type"].startswith("text/css")
    assert favicon.status_code == 200
    assert favicon.headers["content-type"].startswith("image/svg+xml")
    assert health.status_code == 200 and health.json() == {"status": "healthy"}
    assert capabilities.status_code == 200


def test_client_side_routes_fall_back_to_the_spa_document(project_tmp_path):
    dist = _write_fake_build(project_tmp_path)
    with _client(project_tmp_path, dist) as client:
        workspaces = client.get("/workspaces")
        deep_route = client.get("/wiki/pages/some-page")

    for response in (workspaces, deep_route):
        assert response.status_code == 200
        assert SPA_MARKER in response.text


def test_unknown_api_paths_keep_json_error_semantics(project_tmp_path):
    dist = _write_fake_build(project_tmp_path)
    with _client(project_tmp_path, dist) as client:
        missing = client.get("/api/v1/not-a-real-endpoint")
        missing_asset = client.get("/assets/does-not-exist.js")

    assert missing.status_code == 404
    assert missing.json() == {"detail": "Not Found"}
    assert SPA_MARKER not in missing.text
    assert missing_asset.status_code == 404
    assert SPA_MARKER not in missing_asset.text


def test_spa_fallback_cannot_escape_the_build_directory(project_tmp_path):
    dist = _write_fake_build(project_tmp_path)
    secret = project_tmp_path / "secret.txt"
    secret.write_text("TOP_SECRET_CONTENT", encoding="utf-8")

    with _client(project_tmp_path, dist) as client:
        escaped = client.get("/%2e%2e%2fsecret.txt")

    assert escaped.status_code == 200
    assert "TOP_SECRET_CONTENT" not in escaped.text
    assert SPA_MARKER in escaped.text


def test_missing_frontend_build_is_reported_explicitly(project_tmp_path):
    absent = project_tmp_path / "absent-build"
    app = create_app(data_root=project_tmp_path / "api", ui_dist=absent)
    assert app.state.ui_dist is None

    with TestClient(app) as client:
        index = client.get("/")
        health = client.get("/api/v1/health")

    assert index.status_code == 503
    assert index.json()["error"]["code"] == FRONTEND_BUILD_MISSING
    assert "npm run build" in index.json()["error"]["message"]
    assert health.status_code == 200 and health.json() == {"status": "healthy"}


def test_resolve_ui_dist_dir_requires_a_built_index(project_tmp_path):
    empty = project_tmp_path / "empty"
    empty.mkdir()
    assert resolve_ui_dist_dir(empty) is None

    dist = _write_fake_build(project_tmp_path)
    assert resolve_ui_dist_dir(dist) == dist


def test_product_app_mounts_the_formal_ui_not_the_acceptance_panel(project_tmp_path):
    dist = _write_fake_build(project_tmp_path)
    app = create_app(data_root=project_tmp_path / "api", ui_dist=dist)
    mount_names = {getattr(route, "name", None) for route in app.routes}

    assert "ui-assets" in mount_names
    # ``static`` is the Stage 7 acceptance panel mount; it must not be reused here.
    assert "static" not in mount_names


def test_repository_ui_build_is_served_when_present(project_tmp_path):
    repo_root = Path(__file__).resolve().parents[2]
    expected = repo_root / "ui" / "dist"
    if not (expected / "index.html").is_file():
        pytest.skip("ui/dist has not been built in this checkout")

    assert resolve_ui_dist_dir() == expected

    with TestClient(create_app(data_root=project_tmp_path / "api")) as client:
        index = client.get("/")
        health = client.get("/api/v1/health")

    assert index.status_code == 200
    assert "text/html" in index.headers["content-type"]
    assert health.status_code == 200
