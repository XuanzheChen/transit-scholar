"""Product navigation and explicit user-visible state handling in the UI shell."""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
UI_SRC = REPO_ROOT / "ui" / "src"
NAVIGATION = UI_SRC / "app" / "navigation.ts"
SHELL = UI_SRC / "app" / "AppShell.tsx"
BACKEND_STATUS = UI_SRC / "app" / "BackendStatusContext.tsx"
ASYNC_STATE = UI_SRC / "components" / "AsyncState.tsx"

PRODUCT_LABELS = ("Workspaces", "Research", "Library", "Wiki", "Schemas")
PRODUCT_PATHS = ("/workspaces", "/research", "/library", "/wiki", "/schemas")
INTERNAL_TERMS = ("Layer1", "Layer2", "Layer3", "Ledger", "RoleRuntime", "ResearchSession")


def _navigation_block() -> str:
    text = NAVIGATION.read_text(encoding="utf-8")
    block = text.split("export const PRODUCT_NAV_ITEMS")[1]
    return block.split("export const INTERNAL_TERMS_NOT_IN_NAVIGATION")[0]


def test_primary_navigation_exposes_product_concepts():
    block = _navigation_block()
    for label in PRODUCT_LABELS:
        assert f"label: '{label}'" in block
    for path in PRODUCT_PATHS:
        assert f"path: '{path}'" in block


def test_internal_engineering_concepts_are_not_primary_navigation():
    block = _navigation_block()
    for term in INTERNAL_TERMS:
        assert term not in block, f"{term} leaked into product navigation"

    shell = SHELL.read_text(encoding="utf-8")
    for term in INTERNAL_TERMS:
        assert term not in shell, f"{term} leaked into the application shell"


def test_shell_reports_backend_connectivity_from_the_api():
    backend_status = BACKEND_STATUS.read_text(encoding="utf-8")
    assert "api.system.health" in backend_status
    assert "'/api/v1/health'" not in backend_status, "transport must stay inside src/api"
    for state in ("checking", "connected", "unavailable"):
        assert f"'{state}'" in backend_status

    shell = SHELL.read_text(encoding="utf-8")
    for label in ("Backend connected", "Backend unavailable", "Checking backend"):
        assert label in shell


def test_capabilities_are_surfaced_including_unavailable_ones():
    backend_status = BACKEND_STATUS.read_text(encoding="utf-8")
    assert "api.system.capabilities" in backend_status

    panel = (UI_SRC / "components" / "CapabilityPanel.tsx").read_text(encoding="utf-8")
    assert "Unavailable" in panel
    for capability in (
        "pause_resume",
        "user_schema_creation",
        "base_wiki",
        "agentic_wiki",
        "semantic_wiki_search",
        "pdf_upload_max_bytes",
    ):
        assert capability in panel


def test_shared_state_components_cover_every_required_state():
    async_state = ASYNC_STATE.read_text(encoding="utf-8")
    for component in ("LoadingState", "EmptyState", "ErrorState", "UnavailableState"):
        assert f"export function {component}" in async_state
    # Failures must surface the API-provided code/message, not invented status.
    assert "apiError.code" in async_state
    assert "apiError.message" in async_state


def test_workspace_scoped_sections_require_an_open_workspace():
    gate = (UI_SRC / "features" / "workspaces" / "RequiresWorkspace.tsx").read_text(encoding="utf-8")
    assert "activeWorkspaceId === null" in gate
    for view in ("conversations/ResearchView.tsx", "wiki/WikiView.tsx"):
        text = (UI_SRC / "features" / view).read_text(encoding="utf-8")
        assert "RequiresWorkspace" in text


def test_active_workspace_selection_is_not_authoritative_backend_state():
    context = (UI_SRC / "app" / "ActiveWorkspaceContext.tsx").read_text(encoding="utf-8")
    assert "localStorage" in context
    # The stored value is only an identifier; workspace data is always fetched.
    assert "workspace_id" in context or "workspaceId" in context
