from transit_scholar.layer2.schema_catalog import SchemaCatalog, SchemaVersionExistsError


def _draft(schema_id="user_transit", version="1.0"):
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


def test_catalog_lists_builtins_and_persisted_user_schema(tmp_path):
    catalog = SchemaCatalog(tmp_path)
    created = catalog.create(_draft())

    listed = {(item.schema_id, item.version) for item in catalog.list()}

    assert (created.schema_id, created.version) in listed
    assert ("bus_control_rl", "1.0") in listed


def test_validation_is_non_persistent(tmp_path):
    catalog = SchemaCatalog(tmp_path)

    valid, issues, definition = catalog.validate(_draft())

    assert valid is True
    assert issues == []
    assert definition is not None
    assert not (tmp_path / "schemas").exists()


def test_duplicate_version_is_rejected_and_created_schema_resolves(tmp_path):
    catalog = SchemaCatalog(tmp_path)
    catalog.create(_draft())

    assert catalog.resolve("user_transit", "1.0").schema_id == "user_transit"
    try:
        catalog.create(_draft())
    except SchemaVersionExistsError:
        pass
    else:
        raise AssertionError("expected immutable version conflict")


def test_invalid_schema_identity_is_non_persistent(tmp_path):
    catalog = SchemaCatalog(tmp_path)

    valid, issues, definition = catalog.validate(_draft(schema_id="../outside"))

    assert valid is False
    assert issues
    assert definition is None
    assert not (tmp_path / "schemas").exists()
