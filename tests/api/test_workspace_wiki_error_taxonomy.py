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
    (WikiUnsupportedError, 409), (WikiMissingError, 409), (WikiStaleError, 409),
    (WikiCorruptError, 409), (WikiEmptyMembershipError, 409),
    (WorkspaceNotFoundError, 404), (WorkspaceNotActiveError, 409), (WorkspaceError, 500),
])
def test_wiki_state_error_contract(project_tmp_path, operation, error_type, status):
    _assert_endpoint(project_tmp_path, error_type, status, operation)


def _assert_endpoint(root, error_type, status, operation):
    calls = []
    message = 'State does not permit this operation' if status < 500 else r'SECRET password=abc C:\private\db.sqlite'
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
    public_message = message if status < 500 else ('Workspace operation failed' if operation == 'schema' else 'Wiki operation failed')
    assert response.json() == {'error': {'code': code, 'message': public_message, 'details': {'workspace_id': 'w'}}}
    if status == 500:
        assert 'SECRET' not in response.text
        assert 'password=' not in response.text
        assert 'private' not in response.text


def test_real_no_schema_workspace_rejects_wiki_build_with_conflict(project_tmp_path):
    with TestClient(create_app(data_root=project_tmp_path)) as client:
        workspace = client.post('/api/v1/workspaces', json={'name': 'No schema'}).json()
        response = client.post(f'/api/v1/workspaces/{workspace["workspace_id"]}/wiki/build')
        assert response.status_code == 409
        assert response.json()['error']['code'] == 'WIKI_UNSUPPORTED'
