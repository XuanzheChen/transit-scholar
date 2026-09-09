from fastapi.testclient import TestClient
import fitz

from transit_scholar.api import create_app
from transit_scholar.config import settings


def test_import_final_gate_uses_owned_root_after_global_changes(project_tmp_path, monkeypatch):
    import transit_scholar.workflow.service as workflow
    root = project_tmp_path / 'owned'
    app = create_app(data_root=root)
    observed = []
    original = workflow.get_second_layer_input
    def gate(paper_id, **kwargs):
        observed.append(kwargs)
        return original(paper_id, **kwargs)
    monkeypatch.setattr(workflow, 'get_second_layer_input', gate)
    monkeypatch.setattr(settings, 'metadata_enrichment_allow_network', False)
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), 'Transit Study\nAlice Smith\nDOI: 10.1234/freeze.2026\nAbstract: Transit scheduling research.')
    doc.set_metadata({'title': 'Transit Study', 'author': 'Alice Smith'})
    pdf = doc.tobytes()
    doc.close()
    with TestClient(app) as client:
        monkeypatch.setattr(settings, 'data_root', project_tmp_path / 'unrelated')
        response = client.post('/api/v1/papers/import', files={'file': ('study.pdf', pdf, 'application/pdf')})
        assert response.status_code == 201
        assert response.json()['status'] == 'completed'
        assert len(observed) == 1
        assert observed[0]['data_root'] == root
        assert observed[0]['session_factory'] is app.state.runtime_context.session_factory
        paper_id = response.json()['paper_id']
        assert client.get(f'/api/v1/papers/{paper_id}').status_code == 200
        assert not (project_tmp_path / 'unrelated').exists()
