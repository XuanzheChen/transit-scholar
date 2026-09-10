"""Real API paths for disabled Schema reads and missing bound Schema content."""
import pymupdf
import pytest
from fastapi.testclient import TestClient

from transit_scholar.api import create_app
from transit_scholar.layer3.schema.errors import SchemaMissingError


def _import(client):
    with pymupdf.open() as doc:
        page = doc.new_page()
        page.insert_text((72, 72), 'Transit Schema Boundary Study\nAlice Smith\nAbstract: Transit scheduling.')
        doc.set_metadata({'title': 'Transit Schema Boundary Study', 'author': 'Alice Smith'})
        pdf = doc.tobytes()
    response = client.post('/api/v1/papers/import', files={'file': ('boundary.pdf', pdf, 'application/pdf')})
    assert response.status_code == 201
    assert response.json()['paper_id']
    return response.json()['paper_id']


@pytest.mark.parametrize('paper_state', ['member', 'nonmember', 'missing'])
def test_no_schema_paper_schema_read(project_tmp_path, paper_state):
    with TestClient(create_app(data_root=project_tmp_path)) as client:
        response = client.post('/api/v1/workspaces', json={'name': 'No schema'})
        assert response.status_code == 201
        workspace_id = response.json()['workspace_id']
        paper_id = _import(client) if paper_state != 'missing' else 'missing-paper'
        if paper_state == 'member':
            assert client.post(f'/api/v1/workspaces/{workspace_id}/papers', json={'paper_id': paper_id}).status_code == 200
        response = client.get(f'/api/v1/workspaces/{workspace_id}/papers/{paper_id}/schema')
        if paper_state == 'member':
            assert response.status_code == 200
            assert response.json() == {'workspace_id': workspace_id, 'paper_id': paper_id,
                                       'status': 'disabled', 'error_code': 'schema_disabled'}
        else:
            assert response.status_code == 404
            assert response.json()['error']['code'] == 'NOT_FOUND'


def test_bound_wiki_build_missing_schema_hides_storage_diagnostics(project_tmp_path):
    app = create_app(data_root=project_tmp_path)
    with TestClient(app) as client:
        draft = {'schema_id': 'boundary_schema', 'version': '1.0', 'sections': [
            {'id': 'overview', 'label': 'Overview', 'fields': [
                {'id': 'summary', 'label': 'Summary', 'question': 'Summarize', 'type': 'string'}]}]}
        assert client.post('/api/v1/schemas', json=draft).status_code == 201
        created = client.post('/api/v1/workspaces', json={'name': 'Bound', 'schema': {'schema_id': 'boundary_schema', 'version': '1.0'}})
        assert created.status_code == 201
        workspace_id = created.json()['workspace_id']
        paper_id = _import(client)
        assert client.post(f'/api/v1/workspaces/{workspace_id}/papers', json={'paper_id': paper_id}).status_code == 200
        # Prove the real lower layer contains the diagnostics the API must hide.
        product = app.state.runtime_context.create_product()
        try:
            with pytest.raises(SchemaMissingError) as raised:
                product.build_workspace_wiki(workspace_id)
            assert 'current.json' in str(raised.value)
            assert 'SchemaCurrentNotFoundError' in str(raised.value)
        finally:
            product.close()
        response = client.post(f'/api/v1/workspaces/{workspace_id}/wiki/build')
        assert response.status_code == 409
        assert response.json() == {'error': {'code': 'SCHEMA_MISSING',
            'message': 'Workspace schema content is unavailable', 'details': {'workspace_id': workspace_id}}}
        for diagnostic in (str(project_tmp_path), project_tmp_path.as_posix(), project_tmp_path.name, 'current.json', 'SchemaCurrentNotFoundError'):
            assert diagnostic not in response.text
