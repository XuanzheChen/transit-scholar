"""Schema catalog and Schema Builder UI behavior (T-008).

These tests pin the user-visible contract of the Schema Builder iteration to the
implementation:

* the Schema area lists the Schema definitions and versions the API returns and
  shows one version read-only, with no edit-in-place action (C-008);
* the builder is a structured draft form supporting identity, version, name,
  description, Sections, and Fields, with add/remove actions for both;
* the draft is validated through the existing validate endpoint before creation
  is offered, and validation issues are rendered beside the draft content they
  belong to (AC-013);
* immediately before creation the user must confirm that the version is
  immutable (REQ-011);
* the field type vocabulary and endpoint paths stay aligned with the frozen
  backend contract, and the frontend only reaches the API through the shared
  transport module.
"""
import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
UI_ROOT = REPO_ROOT / "ui"
UI_SRC = UI_ROOT / "src"
FEATURES = UI_SRC / "features"
SCHEMAS = FEATURES / "schemas"

VIEW = "features/schemas/SchemasView.tsx"
BUILDER = "features/schemas/SchemaBuilderDialog.tsx"
EDITOR = "features/schemas/SchemaDraftEditor.tsx"
DETAIL = "features/schemas/SchemaVersionDetailView.tsx"
DRAFT = "features/schemas/schemaDraft.ts"
LABELS = "features/schemas/labels.ts"

SCHEMA_ROUTER = REPO_ROOT / "src" / "transit_scholar" / "api" / "routers" / "schemas.py"
SCHEMA_MODELS = REPO_ROOT / "src" / "transit_scholar" / "layer2" / "schema_extraction" / "models.py"
API_CLIENT = "api/client.ts"


def _read(relative: str) -> str:
    return (UI_SRC / relative).read_text(encoding="utf-8")


def _package() -> dict:
    return json.loads((UI_ROOT / "package.json").read_text(encoding="utf-8"))


# ----------------------------------------------------------------- catalog


def test_schema_catalog_lists_definitions_and_versions():
    view = _read(VIEW)
    assert "api.schemas.list(" in view
    assert 'data-testid="schema-catalog-list"' in view
    assert "schema-catalog-item-" in view
    # Every catalog entry states the Schema identity, version, and content hash
    # it received from the API.
    assert "schema.schema_id" in view
    assert "version {schema.version}" in view
    assert "schema.schema_hash" in view


def test_schema_version_detail_is_read_only():
    view = _read(VIEW)
    detail = _read(DETAIL)

    assert "api.schemas.get(" in detail
    assert 'data-testid="schema-version-detail"' in detail
    assert 'data-testid="schema-version-immutable"' in detail
    assert "readDefinitionSections(" in detail
    # The detail route resolves the version identity from the URL.
    assert "parseVersionRoute(" in view
    assert "versions !== 'versions'" in view
    assert "schemaVersionHref(" in view
    # No creation or validation is offered from the read-only detail view.
    assert "api.schemas.create(" not in detail
    assert "api.schemas.validate(" not in detail


def test_no_edit_in_place_for_an_existing_schema_version():
    # There is no Schema update endpoint in the frontend boundary at all.
    for relative in (VIEW, BUILDER, EDITOR, DETAIL, DRAFT, LABELS):
        text = _read(relative)
        for forbidden in ("api.schemas.update", "api.schemas.patch", "api.schemas.delete"):
            assert forbidden not in text, f"{relative} adds an edit-in-place Schema call"
    assert "api.schemas.update" not in _read(API_CLIENT)

    # The catalog states why no edit action exists instead of offering one.
    assert "no edit action" in _read(VIEW)
    assert "never edited" in _read(DETAIL)


# ----------------------------------------------------------- structured draft


def test_builder_provides_a_structured_draft_form():
    editor = _read(EDITOR)
    for test_id in (
        "schema-id-input",
        "schema-version-input",
        "schema-name-input",
        "schema-description-input",
    ):
        assert test_id in editor, f"the draft form is missing {test_id}"
    assert "schema-section-id-" in editor
    assert "schema-section-label-" in editor
    assert "schema-field-id-" in editor
    assert "schema-field-label-" in editor
    assert "schema-field-question-" in editor
    assert "schema-field-type-" in editor


def test_builder_supports_adding_and_removing_sections_and_fields():
    editor = _read(EDITOR)
    draft = _read(DRAFT)

    assert 'data-testid="schema-add-section"' in editor
    assert "schema-add-field-" in editor
    assert "schema-remove-section-" in editor
    assert "schema-remove-field-" in editor

    for helper in (
        "export function withSectionAdded",
        "export function withSectionRemoved",
        "export function withFieldAdded",
        "export function withFieldRemoved",
    ):
        assert helper in draft, f"the draft model is missing {helper}"


def test_draft_conversion_matches_the_frozen_definition_shape():
    draft = _read(DRAFT)
    for key in (
        "schema_id",
        "version",
        "sections",
        "id:",
        "label:",
        "question:",
        "description:",
        "type:",
        "evidence_required:",
        "allow_inference:",
    ):
        assert key in draft, f"the draft conversion omits {key}"
    # Enum options are only attached for enum fields.
    assert "field.type === 'enum'" in draft
    assert "parseOptions(" in draft


# ---------------------------------------------------------- validate/create


def test_creation_calls_the_validate_endpoint_before_create():
    builder = _read(BUILDER)
    assert "api.schemas.validate(" in builder
    assert "api.schemas.create(" in builder
    assert builder.index("api.schemas.validate(") < builder.index("api.schemas.create(")

    # Creation is gated on the API validation result for the current draft.
    assert "draftIsValidated" in builder
    assert "validation?.valid === true" in builder
    assert "validatedFingerprint === requestFingerprint" in builder
    assert "disabled={!confirmed || !draftIsValidated}" in builder


def test_validation_issues_are_shown_beside_the_draft_content():
    editor = _read(EDITOR)
    builder = _read(BUILDER)
    draft = _read(DRAFT)

    assert "issuesForField(" in editor
    assert "issuesForSection(" in editor
    assert "schema-field-issues-" in editor
    assert "schema-section-issues-" in editor
    assert "describeIssue(" in editor
    assert 'data-testid="schema-validation-summary"' in builder
    assert 'data-testid="schema-validation-issue"' in builder
    assert 'data-testid="schema-validation-rejected"' in builder
    assert "readIssueLocation(" in draft


def test_invalid_draft_cannot_be_created():
    builder = _read(BUILDER)
    # While the API reports the draft invalid, the view says so and creation
    # stays unavailable.
    assert "Creation stays unavailable until validation succeeds" in builder
    assert 'data-testid="schema-builder-confirm"' in builder


# ------------------------------------------------------- immutability rules


def test_immutability_guidance_and_final_confirmation_are_present():
    editor = _read(EDITOR)
    builder = _read(BUILDER)
    labels = _read(LABELS)

    assert 'data-testid="schema-immutability-guidance"' in editor
    assert "SCHEMA_VERSION_IMMUTABLE_STATEMENT" in editor

    assert "SCHEMA_VERSION_IMMUTABLE_STATEMENT" in labels
    assert "SCHEMA_VERSION_CONFIRMATION_LABEL" in labels
    assert "cannot be edited" in labels
    assert "new version" in labels

    assert 'data-testid="schema-version-immutable-warning"' in builder
    assert 'data-testid="schema-immutable-confirm"' in builder
    assert "confirmed" in builder
    # The created version is reported as immutable, never as editable.
    assert "was created and is immutable" in _read(VIEW)


# -------------------------------------------------------- API capabilities


def test_schema_creation_is_gated_on_the_capability_api():
    view = _read(VIEW)
    assert "user_schema_creation" in view
    assert "api.system.capabilities(" in _read("api/endpoints/system.ts") or "useBackendStatus" in view
    assert 'data-testid="schema-creation-unavailable"' in view
    assert "disabled={!creationAvailable}" in view


# ------------------------------------------------------ contract alignment


def test_field_type_vocabulary_matches_the_backend_contract():
    models = SCHEMA_MODELS.read_text(encoding="utf-8")
    literal = re.search(r"FieldType = Literal\[(.*?)\]", models, re.S)
    assert literal, "the FieldType vocabulary was not found in the backend models"
    backend_types = sorted(set(re.findall(r'"([a-z]+)"', literal.group(1))))

    draft = _read(DRAFT)
    declaration = re.search(r"SCHEMA_FIELD_TYPES = \[(.*?)\] as const", draft, re.S)
    assert declaration, "SCHEMA_FIELD_TYPES was not found in the draft model"
    ui_types = sorted(set(re.findall(r"'([a-z]+)'", declaration.group(1))))

    assert ui_types == backend_types, (
        f"UI field types {ui_types} do not match the backend vocabulary {backend_types}"
    )


def test_schema_endpoint_paths_match_the_frozen_router():
    router = SCHEMA_ROUTER.read_text(encoding="utf-8")
    endpoints = _read("api/endpoints/schemas.ts")

    assert '@router.post("/schemas/validate"' in router
    assert "'/api/v1/schemas/validate'" in endpoints
    assert '@router.post("/schemas",' in router
    assert "'/api/v1/schemas'" in endpoints
    assert '@router.get("/schemas"' in router
    assert "'/api/v1/schemas'" in endpoints
    assert "@router.get(\"/schemas/{schema_id}/versions/{version}\"" in router
    assert "/versions/${encodeURIComponent(version)}" in endpoints
    # The existing-version conflict keeps its frozen code in the UI taxonomy.
    assert "SCHEMA_VERSION_EXISTS" in router
    assert "SCHEMA_VERSION_EXISTS" in _read(API_CLIENT)


def test_schema_feature_stays_on_the_shared_api_boundary():
    for source in SCHEMAS.rglob("*.ts*"):
        text = source.read_text(encoding="utf-8")
        assert "fetch(" not in text, f"{source.name} bypasses the shared API module"
        assert "XMLHttpRequest" not in text, f"{source.name} bypasses the shared API module"


def test_schema_ui_does_not_expose_internal_layer_terms():
    for source in SCHEMAS.rglob("*.ts*"):
        text = source.read_text(encoding="utf-8")
        for term in ("Layer1", "Layer2", "Layer3", "RoleRuntime", "ResearchSession"):
            assert term not in text, f"{source.name} exposes the internal term {term}"


# ------------------------------------------------------------------ evidence


def test_live_schema_smoke_harness_is_available():
    script = UI_ROOT / "scripts" / "smoke-schemas.mjs"
    assert script.is_file()
    body = script.read_text(encoding="utf-8")
    for marker in (
        "schema-catalog-list",
        "schema-version-detail",
        "schema-version-immutable",
        "new-schema-button",
        "schema-builder-dialog",
        "schema-add-section",
        "schema-add-field-",
        "schema-validate-submit",
        "schema-field-issues-",
        "schema-validation-rejected",
        "schema-immutable-confirm",
        "schema-create-submit",
        "schema-created-banner",
        "schema-creation-unavailable",
        "schema-select",
        "create-workspace-submit",
    ):
        assert marker in body, f"the Schema smoke does not exercise {marker}"
    assert "smoke:schemas" in _package()["scripts"]
    assert "smoke-schemas.mjs" in _package()["scripts"]["smoke:schemas"]
