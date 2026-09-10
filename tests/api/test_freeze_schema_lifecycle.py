"""Freeze lifecycle proof with real extraction, persistence and Wiki build."""
from fastapi.testclient import TestClient

from transit_scholar.api import create_app
import fitz
from transit_scholar.layer2.schema_extraction import FakeLLMProvider
from transit_scholar.layer3.storage import workspace_layout, read_build_provenance
from transit_scholar.product.facade import TransitScholarProduct
from test_freeze_runtime_integration import context_for, freeze_root
from test_schema_catalog_api import _draft


def test_schema_10_then_11_http_materialization_and_wiki_keep_original_identity(freeze_root, monkeypatch):
    import transit_scholar.layer3.schema.service as schema_module
    from test_l3s1_wiki_workspace import _offline_composition
    original_extract = schema_module.extract_schema
    extracted = []

    def extract(*args, **kwargs):
        assert kwargs['definition'].version == '1.0'
        result = original_extract(*args, **kwargs, llm_client=FakeLLMProvider())
        extracted.append(result)
        return result

    monkeypatch.setattr(schema_module, 'extract_schema', extract)
    original_wiki = TransitScholarProduct._workspace_wiki

    def wiki(product):
        service = original_wiki(product)
        service._composition_factory = _offline_composition
        return service

    monkeypatch.setattr(TransitScholarProduct, '_workspace_wiki', wiki)
    from transit_scholar.config import settings
    monkeypatch.setattr(settings, "metadata_enrichment_allow_network", False)
    context = context_for(freeze_root)
    context.settings.metadata_enrichment_allow_network = False
    app = create_app(runtime_context=context)
    try:
        with TestClient(app) as client:
            created = client.post('/api/v1/schemas', json=_draft('foo', '1.0'))
            assert created.status_code == 201, created.text
            original_hash = created.json()['schema_hash']
            created = client.post('/api/v1/workspaces', json={'name': 'A', 'schema': {'schema_id': 'foo', 'version': '1.0'}})
            assert created.status_code == 201, created.text
            workspace = created.json()['workspace_id']
            document = fitz.open()
            page = document.new_page()
            page.insert_text((72, 72), 'Freeze Transit Study\nAlice Smith\nDOI: 10.1234/freeze.2026\nAbstract: Transit scheduling research.')
            document.set_metadata({'title': 'Freeze Transit Study', 'author': 'Alice Smith'})
            pdf = document.tobytes()
            document.close()
            imported = client.post('/api/v1/papers/import', files={'file': ('study.pdf', pdf, 'application/pdf')})
            assert imported.status_code == 201, imported.text
            assert imported.json()['status'] == 'completed', imported.text
            paper_id = imported.json()['paper_id']
            assert client.get('/api/v1/papers').status_code == 200
            assert client.get(f'/api/v1/papers/{paper_id}').status_code == 200
            assert client.post(f'/api/v1/workspaces/{workspace}/papers', json={'paper_id': paper_id}).status_code in (200, 201)
            newer = _draft('foo', '1.1')
            newer['sections'][0]['fields'][0]['question'] = 'A different interpretation?'
            assert client.post('/api/v1/schemas', json=newer).status_code == 201
            materialized = client.post(f'/api/v1/workspaces/{workspace}/papers/{paper_id}/schema/materialize')
            assert materialized.status_code == 200, materialized.text
            storage = workspace_layout(workspace, data_root=freeze_root).schema_storage()
            manifest = storage.read_run(paper_id, extracted[0].run_manifest.run_id).run_manifest
            assert materialized.json()['status'] == manifest.status
            assert materialized.json()['status'] is not None
            assert manifest.schema_version == '1.0'
            assert manifest.schema_hash == original_hash
            built = client.post(f'/api/v1/workspaces/{workspace}/wiki/build')
            assert built.status_code == 200, built.text
            binding = client.get(f'/api/v1/workspaces/{workspace}').json()['schema_binding']
            assert binding == {'schema_id': 'foo', 'schema_version': '1.0', 'schema_hash': original_hash}
            product = context.create_product()
            try:
                snapshot = product._workspace_wiki().schemas.capture_build_snapshot(workspace)
                assert snapshot.definition.version == snapshot.schema_version == '1.0'
                assert snapshot.schema_hash == original_hash
            finally:
                product.close()
            provenance = read_build_provenance(workspace_layout(workspace, data_root=freeze_root).wiki_dir)
            assert snapshot.runs_by_paper[paper_id].schema_version == '1.0'
            assert provenance.schema_runs[paper_id]['schema_hash'] == original_hash
    finally:
        context.session_factory.kw['bind'].dispose()
