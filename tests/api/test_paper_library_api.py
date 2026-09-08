from __future__ import annotations

from io import BytesIO

import pytest
from fastapi import UploadFile

from transit_scholar.api import create_app
from transit_scholar.api.errors import ApiError
from transit_scholar.api.routers import papers as paper_router
from transit_scholar.config import settings
from transit_scholar.workflow.result import ImportPipelineResult


def test_pdf_import_uses_ingestion_workflow(monkeypatch):
    captured = {}

    def import_pipeline(path):
        captured["content"] = path.read_bytes()
        return ImportPipelineResult(
            status="completed", job_id="job", paper_id="paper", file_id="file",
            is_exact_duplicate=False, import_status="accepted", metadata_status="extracted",
            duplicate_status="completed", relations_created=0, relations_existing=0,
            relation_ids=[], current_stage="completed", error_code=None, error_message=None,
            warnings=[], second_layer_ready=True, second_layer_blockers=[],
        )

    class Product:
        def import_paper(self, path):
            return import_pipeline(path)

    response = paper_router.import_paper(
        UploadFile(filename="valid.pdf", file=BytesIO(b"%PDF-1.4\nvalid"), headers={"content-type": "application/pdf"}), Product()
    )
    assert captured["content"].startswith(b"%PDF")
    assert response.paper_id == "paper"
    assert response.second_layer_ready is True


def test_oversized_pdf_is_rejected_before_ingestion(monkeypatch):
    monkeypatch.setattr(settings, "max_file_size_bytes", 4)
    class Product:
        def import_paper(self, path):
            pytest.fail("ingestion must not run")

    with pytest.raises(ApiError) as raised:
        paper_router.import_paper(
            UploadFile(filename="large.pdf", file=BytesIO(b"%PDF-too-large"), headers={"content-type": "application/pdf"}), Product()
        )
    assert raised.value.status_code == 413
    assert raised.value.code == "UPLOAD_TOO_LARGE"


def test_file_content_contract_accepts_only_registered_file_identity(project_tmp_path):
    app = create_app(data_root=project_tmp_path / "api")
    operation = app.openapi()["paths"]["/api/v1/files/{file_id}/content"]["get"]
    assert [item["name"] for item in operation["parameters"]] == ["file_id"]
    assert "path" not in operation
