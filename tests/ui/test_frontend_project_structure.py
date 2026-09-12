"""The formal UI must be a standalone React/TypeScript/Vite application."""
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
UI_ROOT = REPO_ROOT / "ui"


def _package() -> dict:
    return json.loads((UI_ROOT / "package.json").read_text(encoding="utf-8"))


def test_frontend_is_a_separate_react_typescript_vite_application():
    package = _package()
    assert "react" in package["dependencies"]
    assert "react-dom" in package["dependencies"]
    dev_dependencies = package["devDependencies"]
    for dependency in ("vite", "typescript", "@vitejs/plugin-react"):
        assert dependency in dev_dependencies
    assert "tsc -b" in package["scripts"]["build"]
    assert "vite build" in package["scripts"]["build"]


def test_frontend_has_a_typescript_entry_point_and_local_title():
    assert (UI_ROOT / "tsconfig.json").is_file()
    assert (UI_ROOT / "tsconfig.app.json").is_file()
    index = (UI_ROOT / "index.html").read_text(encoding="utf-8")
    assert "/src/main.tsx" in index
    assert "<title>TransitScholar</title>" in index


def test_frontend_build_is_origin_relative():
    index = (UI_ROOT / "index.html").read_text(encoding="utf-8")
    assert "http://" not in index
    assert "https://" not in index


def test_vite_dev_proxies_the_api_and_the_build_emits_static_assets():
    config = (UI_ROOT / "vite.config.ts").read_text(encoding="utf-8")
    assert "'/api/v1'" in config
    assert "TRANSIT_SCHOLAR_API_TARGET" in config
    assert "outDir: 'dist'" in config


def test_frontend_is_organized_by_product_feature():
    features = UI_ROOT / "src" / "features"
    for feature in ("workspaces", "conversations", "library", "wiki", "schemas"):
        assert (features / feature).is_dir(), f"missing feature directory: {feature}"
        assert any((features / feature).iterdir()), f"empty feature directory: {feature}"


def test_backend_connection_state_smoke_harness_is_available():
    script = UI_ROOT / "scripts" / "smoke-backend-states.mjs"
    assert script.is_file()
    body = script.read_text(encoding="utf-8")
    assert "Backend unavailable" in body
    assert "Backend connected" in body

    package = _package()
    assert "smoke:states" in package["scripts"]
    assert "jsdom" in package["devDependencies"]


def test_frontend_does_not_extend_the_legacy_acceptance_panel():
    legacy_markers = ("stage7", "stage 7", "acceptance panel", "web/static", "transit_scholar.web")
    for source in (UI_ROOT / "src").rglob("*"):
        if not source.is_file():
            continue
        text = source.read_text(encoding="utf-8").lower()
        for marker in legacy_markers:
            assert marker not in text, f"{source.name} references the legacy acceptance panel ({marker})"
