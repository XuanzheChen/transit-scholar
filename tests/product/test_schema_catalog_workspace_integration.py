from transit_scholar.product.facade import TransitScholarProduct


def _draft():
    return {
        "schema_id": "workspace_user_schema",
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


def test_created_user_schema_resolves_for_workspace_creation(session, tmp_path):
    product = TransitScholarProduct(session, runtime_factory=None, data_root=tmp_path)
    definition = product.schema_catalog.create(_draft())

    workspace = product.create_workspace(
        "User schema workspace",
        schema_id=definition.schema_id,
        schema_version=definition.version,
    )

    assert workspace.schema_binding is not None
    assert workspace.schema_binding.schema_id == definition.schema_id
    assert workspace.schema_binding.schema_version == definition.version
    assert workspace.schema_binding.schema_hash == product.describe_schema(definition)["schema_hash"]
