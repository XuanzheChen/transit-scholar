"""Workspace creation/selection and basic Library UI behavior (T-002).

These tests pin the user-visible contract of the first workspace/library
iteration to the implementation:

* Workspace creation supports both no-Schema and existing-Schema modes, and the
  permanent Schema binding warning is rendered before the create action.
* An existing Workspace never offers an editable Schema binding.
* The Library lists API papers, imports a PDF through the frozen upload
  endpoint and its advertised limit, and opens the registered local PDF.
* Paper detail stays user-focused, and "remove from workspace" is a different
  action from "delete from Library".
"""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
UI_ROOT = REPO_ROOT / "ui"
UI_SRC = UI_ROOT / "src"
FEATURES = UI_SRC / "features"
API_ENDPOINTS = UI_SRC / "api" / "endpoints"


def _read(relative: str) -> str:
    return (UI_SRC / relative).read_text(encoding="utf-8")


def _package() -> dict:
    import json

    return json.loads((UI_ROOT / "package.json").read_text(encoding="utf-8"))


CREATE_DIALOG = "features/workspaces/CreateWorkspaceDialog.tsx"
SCHEMA_SELECTION = "features/workspaces/SchemaSelection.tsx"
WORKSPACE_SETTINGS = "features/workspaces/WorkspaceSettingsView.tsx"
WORKSPACE_PAPERS = "features/workspaces/WorkspacePapersPanel.tsx"
LIBRARY_VIEW = "features/library/LibraryView.tsx"
IMPORT_DIALOG = "features/library/ImportPaperDialog.tsx"
PAPER_DETAIL = "features/library/PaperDetailView.tsx"
PAPER_MEMBERSHIP = "features/library/PaperWorkspaceMembership.tsx"


# ---------------------------------------------------------------- workspaces


def test_workspace_creation_supports_no_schema_and_existing_schema():
    dialog = _read(CREATE_DIALOG)
    assert "value: 'none'" in dialog
    assert "value: 'schema'" in dialog
    # Creation goes through the product API, and a Schema choice is sent as the
    # frozen schema_id/version pair (no frontend interpretation of the Schema).
    assert "api.workspaces.create(" in dialog
    assert "schema_id: schema.schemaId" in dialog
    assert "version: schema.version" in dialog


def test_schema_binding_warning_is_rendered_before_the_create_action():
    dialog = _read(CREATE_DIALOG)
    # The warning belongs to the Schema-binding branch of the form, so it is on
    # screen before the create action is enabled, and creation stays disabled
    # until a Schema version is actually selected.
    schema_branch = dialog.split("schemaMode === 'schema' ? (")[1].split(") : (")[0]
    assert 'data-testid="schema-binding-warning"' in schema_branch
    assert "This Schema binding is permanent" in dialog
    assert "cannot be changed later" in dialog
    assert 'data-testid="schema-binding-section"' in dialog
    assert "schemaMissing = schemaMode === 'schema' && !selection" in dialog
    assert "!schemaMissing" in dialog


def test_schema_selection_lists_only_existing_schema_versions():
    selection = _read(SCHEMA_SELECTION)
    assert "api.schemas.list(" in selection
    assert 'data-testid="schema-select"' in selection
    # Schema authoring belongs to the later Schema Builder iteration.
    assert "api.schemas.create(" not in selection
    assert "api.schemas.validate(" not in selection


def test_workspace_settings_show_the_binding_as_non_editable():
    settings = _read(WORKSPACE_SETTINGS)
    assert 'data-testid="workspace-schema-settings"' in settings
    assert 'data-testid="workspace-schema-version"' in settings
    assert 'data-testid="workspace-schema-immutable"' in settings
    assert "A Schema binding is immutable" in settings
    assert "no way to switch this" in settings.replace("\n", " ")
    # The binding is displayed as data, never as a form control.
    assert "Select" not in settings
    assert "TextInput" not in settings
    assert "binding.schema_version" in settings


def test_no_view_offers_to_change_an_existing_workspace_schema():
    offenders = []
    for source in (UI_SRC / "features").rglob("*.tsx"):
        text = source.read_text(encoding="utf-8")
        if "workspaces.schema(" in text:
            offenders.append(source.name)
    assert offenders == [], f"a view tries to mutate a Schema binding: {offenders}"


def test_workspace_without_a_schema_is_a_normal_product_state():
    settings = _read(WORKSPACE_SETTINGS)
    assert "UnavailableState" in settings
    assert "This workspace has no Schema binding" in settings
    assert "normal workspace state" in settings


def test_workspace_paper_membership_can_be_added_and_removed():
    panel = _read(WORKSPACE_PAPERS)
    assert "api.workspaces.addPaper(" in panel
    assert "api.workspaces.removePaper(" in panel
    assert 'data-testid="add-workspace-paper-submit"' in panel
    assert 'data-testid="workspace-paper-list"' in panel
    assert "Remove from workspace" in panel
    assert "remains in the global Library" in panel


# ------------------------------------------------------------------- library


def test_library_lists_api_papers_and_opens_the_local_pdf():
    library = _read(LIBRARY_VIEW)
    assert "api.papers.list(" in library
    assert 'data-testid="library-paper-list"' in library
    assert 'data-testid="import-pdf-button"' in library
    assert "api.papers.fileContentUrl(" in library


def test_pdf_import_uses_the_api_upload_endpoint_and_reported_limit():
    dialog = _read(IMPORT_DIALOG)
    assert "api.papers.importPdf(" in dialog
    assert "pdf_upload_max_bytes" in dialog
    assert 'accept="application/pdf,.pdf"' in dialog
    assert 'data-testid="import-paper-file-input"' in dialog


def test_paper_detail_shows_metadata_readiness_pdf_and_membership():
    detail = _read(PAPER_DETAIL)
    assert "api.papers.detail(" in detail
    assert "api.papers.secondLayer(" in detail
    assert 'data-testid="paper-readiness"' in detail
    assert 'data-testid="open-primary-pdf"' in detail
    assert "PaperWorkspaceMembership" in detail
    # Diagnostics stay behind a disclosure rather than leading the page.
    assert "Disclosure" in detail
    assert 'summary="Record details"' in detail


def test_paper_workspace_membership_is_distinct_from_library_deletion():
    membership = _read(PAPER_MEMBERSHIP)
    assert "api.workspaces.addPaper(" in membership
    assert "api.workspaces.removePaper(" in membership
    assert 'data-testid="add-paper-to-workspace"' in membership
    assert 'data-testid="remove-paper-from-workspace"' in membership
    assert "remains in the global\n        Library" in membership or "remains in the global" in membership
    # Library deletion belongs to the extended Library iteration (REQ-009).
    assert "removeFromLibrary" not in membership


def test_library_delete_and_restore_belong_to_the_extended_library_iteration():
    # REQ-009 supersedes the earlier "not in this iteration" restriction: Library
    # deletion and restoration now live in the dedicated lifecycle panel and
    # remain separate from Workspace membership removal.
    lifecycle = _read("features/library/PaperLibraryLifecyclePanel.tsx")
    assert "api.papers.removeFromLibrary(" in lifecycle
    assert "api.papers.restoreToLibrary(" in lifecycle
    assert 'testId="delete-library-confirm-dialog"' in lifecycle
    assert "removeFromLibrary" not in _read(PAPER_MEMBERSHIP)
    assert "removeFromLibrary" not in _read(WORKSPACE_PAPERS)


# ------------------------------------------------------------------ evidence


def test_live_workspace_and_library_smoke_harness_is_available():
    script = UI_ROOT / "scripts" / "smoke-workspace-library.mjs"
    assert script.is_file()
    body = script.read_text(encoding="utf-8")
    for marker in (
        "create-workspace-submit",
        "schema-binding-warning",
        "workspace-schema-immutable",
        "import-paper-submit",
        "paper-workspace-membership",
        "add-paper-to-workspace",
        "remove-paper-from-workspace",
    ):
        assert marker in body, f"live smoke does not exercise {marker}"
    assert "smoke:workspace-library" in _package()["scripts"]
