"""Endpoint regressions for the frozen Workspace/Schema/Wiki error taxonomy."""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from transit_scholar.api import create_app
from transit_scholar.api.dependencies import get_product
from transit_scholar.layer3.schema.errors import SchemaDisabledError, SchemaMissingError, SchemaBindingMismatchError
from transit_scholar.layer3.wiki.errors import (
    WikiUnsupportedError, WikiMissingError, WikiStaleError, WikiCorruptError, WikiEmptyMembershipError,
)
from transit_scholar.layer3.workspace.errors import (
    WorkspaceError, WorkspaceNotFoundError, WorkspaceNotActiveError, PaperNotFoundError,
    PaperNotMemberError, InvalidWorkspaceInputError, SchemaBindingImmutableError,
)


@pytest.mark.parametrize('error_type,status', [
    (SchemaDisabledError, 409), (SchemaMissingError, 409), (SchemaBindingMismatchError, 409),
    (SchemaBindingImmutableError, 409), (WorkspaceNotActiveError, 409),
    (WorkspaceNotFoundError, 404), (PaperNotFoundError, 404), (PaperNotMemberError, 404),
    (InvalidWorkspaceInputError, 422), (WorkspaceError, 500),
])
def test_schema_materialize_error_contract(project_tmp_path, error_type, status):
    _assert_endpoint(project_tmp_path, error_type, status, 'schema')


@pytest.mark.parametrize('operation', ['build', 'pages'])
@pytest.mark.parametrize('error_type,status', [
    (SchemaMissingError, 409), (SchemaBindingMismatchError, 409),
    (WikiUnsupportedError, 409), (WikiMissingError, 409), (WikiStaleError, 409),
    (WikiCorruptError, 409), (WikiEmptyMembershipError, 409),
    (WorkspaceNotFoundError, 404), (WorkspaceNotActiveError, 409), (WorkspaceError, 500),
])
def test_wiki_state_error_contract(project_tmp_path, operation, error_type, status):
    _assert_endpoint(project_tmp_path, error_type, status, operation)


def _assert_endpoint(root, error_type, status, operation):
    calls = []
    message = r'SECRET password=abc C:\private\db.sqlite current.json SchemaCurrentNotFoundError'
    def fail(*args):
        calls.append(args)
        raise error_type(message)
    product = SimpleNamespace(materialize_workspace_schema=fail, build_workspace_wiki=fail, list_workspace_wiki_pages=fail)
    app = create_app(data_root=root)
    app.dependency_overrides[get_product] = lambda: product
    with TestClient(app) as client:
        if operation == 'schema':
            response = client.post('/api/v1/workspaces/w/papers/p/schema/materialize')
        elif operation == 'build':
            response = client.post('/api/v1/workspaces/w/wiki/build')
        else:
            response = client.get('/api/v1/workspaces/w/wiki/pages')
    assert len(calls) == 1
    assert response.status_code == status
    code = 'NOT_FOUND' if operation == 'schema' and error_type is WorkspaceNotFoundError else error_type.code.upper()
    expected_messages = {
        'schema_disabled': 'Workspace schema is disabled',
        'schema_missing': 'Workspace schema content is unavailable',
        'schema_binding_mismatch': 'Workspace schema binding does not match',
        'schema_binding_immutable': 'Workspace schema binding cannot be changed',
        'workspace_not_active': 'Workspace is not active',
        'workspace_not_found': 'Workspace not found',
        'paper_not_found': 'Paper not found',
        'paper_not_member': 'Paper is not a workspace member',
        'invalid_workspace_input': 'Workspace input is invalid',
        'wiki_unsupported': 'Workspace Wiki is unsupported',
        'wiki_missing': 'Workspace Wiki content is unavailable',
        'wiki_stale': 'Workspace Wiki must be rebuilt',
        'wiki_corrupt': 'Workspace Wiki content is unusable',
        'empty_membership': 'Workspace Wiki requires member papers',
    }
    public_message = expected_messages.get(error_type.code, 'Workspace operation failed' if operation == 'schema' else 'Wiki operation failed')
    assert response.json() == {'error': {'code': code, 'message': public_message, 'details': {'workspace_id': 'w'}}}
    for diagnostic in ('SECRET', 'password=', 'private', 'current.json', 'SchemaCurrentNotFoundError'):
        assert diagnostic not in response.text


def test_real_no_schema_workspace_rejects_wiki_build_with_conflict(project_tmp_path):
    with TestClient(create_app(data_root=project_tmp_path)) as client:
        workspace = client.post('/api/v1/workspaces', json={'name': 'No schema'}).json()
        response = client.post(f'/api/v1/workspaces/{workspace["workspace_id"]}/wiki/build')
        assert response.status_code == 409
        assert response.json()['error']['code'] == 'WIKI_UNSUPPORTED'

@pytest.mark.parametrize('operation,status,code,message', [
    ('catalog_conflict', 409, 'SCHEMA_VERSION_EXISTS', 'Schema version already exists'),
    ('catalog_invalid', 422, 'SCHEMA_INVALID', 'Schema definition is invalid'),
    ('catalog_read', 404, 'NOT_FOUND', 'Schema not found'),
    ('workspace_create', 404, 'NOT_FOUND', 'Schema not found'),
    ('wiki_store', 404, 'NOT_FOUND', 'Wiki resource was not found'),
])
def test_storage_and_catalog_domain_errors_hide_diagnostics(project_tmp_path, operation, status, code, message):
    from transit_scholar.layer2.schema_catalog import SchemaCatalogError, SchemaNotFoundError, SchemaVersionExistsError
    from transit_scholar.layer2.wiki.store import WikiNotFoundError
    types = {'catalog_conflict': SchemaVersionExistsError, 'catalog_invalid': SchemaCatalogError,
             'catalog_read': SchemaNotFoundError, 'workspace_create': SchemaNotFoundError, 'wiki_store': WikiNotFoundError}
    called = []
    def fail(*args, **kwargs):
        called.append(True)
        raise types[operation](r'SECRET password=abc C:\private\current.json')
    product = SimpleNamespace(schema_catalog=SimpleNamespace(create=fail, resolve=fail),
                              describe_schema=lambda value: value, create_workspace=fail, get_workspace_wiki_page=fail)
    app = create_app(data_root=project_tmp_path)
    app.dependency_overrides[get_product] = lambda: product
    with TestClient(app) as client:
        if operation.startswith('catalog_') and operation != 'catalog_read':
            draft = {'schema_id': 'safe', 'version': '1.0', 'sections': []}
            response = client.post('/api/v1/schemas', json=draft)
        elif operation == 'catalog_read':
            response = client.get('/api/v1/schemas/safe/versions/1.0')
        elif operation == 'workspace_create':
            response = client.post('/api/v1/workspaces', json={'name': 'Bound', 'schema': {'schema_id': 'safe', 'version': '1.0'}})
        else:
            response = client.get('/api/v1/workspaces/w/wiki/pages/p')
    assert called == [True]
    assert response.status_code == status
    assert response.json()['error']['code'] == code
    assert response.json()['error']['message'] == message
    for secret in ('SECRET', 'password=', 'private', 'current.json'):
        assert secret not in response.text
