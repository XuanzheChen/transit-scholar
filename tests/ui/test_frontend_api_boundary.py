"""The frontend must consume the frozen ``/api/v1/*`` API through one module.

These tests compare the frontend API boundary against the live backend OpenAPI
document, so any endpoint drift fails loudly instead of silently producing a UI
that cannot reach the backend.
"""
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
UI_ROOT = REPO_ROOT / "ui"
API_DIR = UI_ROOT / "src" / "api"

# ``/api/v1/`` followed by literal path characters or template expressions.
_API_PATH = re.compile(r"/api/v1/(?:\$\{[^}]*\}|[A-Za-z0-9._~%/-])*")
_TEMPLATE_EXPR = re.compile(r"\$\{[^}]*\}")
_PLACEHOLDER = re.compile(r"\{[^}]+\}")


def _normalize(path: str) -> str:
    return _PLACEHOLDER.sub("*", _TEMPLATE_EXPR.sub("*", path)).rstrip("/")


def _frontend_api_paths() -> set[str]:
    paths: set[str] = set()
    for source in API_DIR.rglob("*.ts"):
        text = source.read_text(encoding="utf-8")
        for match in _API_PATH.finditer(text):
            normalized = _normalize(match.group(0))
            # The bare prefix (from doc comments) is not an endpoint.
            if normalized == "/api/v1":
                continue
            paths.add(normalized)
    return paths


def _backend_api_paths(project_tmp_path) -> set[str]:
    from transit_scholar.api.app import create_app

    app = create_app(data_root=project_tmp_path / "api")
    return {
        _normalize(path)
        for path in app.openapi()["paths"]
        if path.startswith("/api/")
    }


def test_frontend_covers_every_frozen_api_path(project_tmp_path):
    missing = sorted(_backend_api_paths(project_tmp_path) - _frontend_api_paths())
    assert missing == [], f"frontend API module does not cover: {missing}"


def test_frontend_references_no_endpoint_the_backend_does_not_expose(project_tmp_path):
    unknown = sorted(_frontend_api_paths() - _backend_api_paths(project_tmp_path))
    assert unknown == [], f"frontend references unknown endpoints: {unknown}"


def test_api_prefix_is_declared_once_in_the_transport_module():
    client = (API_DIR / "client.ts").read_text(encoding="utf-8")
    assert "export const API_PREFIX = '/api/v1'" in client


def test_no_raw_transport_calls_outside_the_api_module():
    offenders: list[str] = []
    for source in (UI_ROOT / "src").rglob("*"):
        if not source.is_file() or source.suffix not in {".ts", ".tsx"}:
            continue
        if API_DIR in source.parents:
            continue
        text = source.read_text(encoding="utf-8")
        if re.search(r"\bfetch\s*\(", text) or "XMLHttpRequest" in text:
            offenders.append(str(source.relative_to(REPO_ROOT)))
    assert offenders == [], f"raw transport calls outside src/api: {offenders}"


def test_frontend_error_handling_uses_the_api_error_envelope():
    client = (API_DIR / "client.ts").read_text(encoding="utf-8")
    assert "class ApiError" in client
    assert "BACKEND_UNAVAILABLE_CODE" in client
    for kind in ("unavailable", "unavailable_capability", "validation", "conflict", "not_found"):
        assert f"'{kind}'" in client
