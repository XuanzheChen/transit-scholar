from __future__ import annotations

from io import BytesIO

import pytest
from fastapi import UploadFile

from transit_scholar.api import create_app
from transit_scholar.api.errors import ApiError
from transit_scholar.api.routers import papers as paper_router
from transit_scholar.config import settings
from transit_scholar.product.errors import ProductPayloadTooLargeError
from transit_scholar.identity.result import PaperActionResult
from transit_scholar.workflow.result import ImportPipelineResult


def test_enrichment_projects_real_provider_result_without_internal_attributes():
    from transit_scholar.doi_enrichment.result import EnrichmentJobResult, ProviderResult
    provider = ProviderResult(provider="crossref", status="fetched", http_status=200,
                              attempt_count=2, fields=["title"])
    provider.internal_debug_payload = "SECRET"
    result = paper_router._enrichment(EnrichmentJobResult(
        paper_id="paper", doi="10.1234/example", status="fetched", providers=[provider]))
    public = result.model_dump(mode="json")
    assert public["providers"][0]["provider"] == "crossref"
    assert public["providers"][0]["fields"] == ["title"]
    assert public["providers"][0]["attempt_count"] == 2
    assert "SECRET" not in str(public)


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

    with pytest.raises(ProductPayloadTooLargeError) as raised:
        paper_router.import_paper(
            UploadFile(filename="large.pdf", file=BytesIO(b"%PDF-too-large"), headers={"content-type": "application/pdf"}), Product()
        )
    assert str(raised.value) == "PDF exceeds configured upload limit"


def test_file_content_contract_accepts_only_registered_file_identity(project_tmp_path):
    app = create_app(data_root=project_tmp_path / "api")
    operation = app.openapi()["paths"]["/api/v1/files/{file_id}/content"]["get"]
    assert [item["name"] for item in operation["parameters"]] == ["file_id"]
    assert "path" not in operation


@pytest.mark.parametrize(
    ("error_code", "expected_code", "expected_status"),
    [
        ("PAPER_NOT_FOUND", "NOT_FOUND", 404),
        ("INVALID_STATE", "INVALID_STATE", 409),
        ("INVALID_FIELDS", "VALIDATION_ERROR", 422),
    ],
)
def test_paper_action_errors_preserve_stable_http_categories(
    error_code, expected_code, expected_status,
):
    result = PaperActionResult(
        paper_id="paper", status="failed", updated_fields=[], audit_log_id=None,
        error_code=error_code, error_message="workflow failure",
    )

    with pytest.raises(ApiError) as raised:
        paper_router._action(result)

    assert raised.value.code == expected_code
    assert raised.value.status_code == expected_status


def test_paper_action_hides_internal_error_message():
    result = PaperActionResult(
        paper_id="paper", status="failed", updated_fields=[], audit_log_id=None,
        error_code="DATABASE_WRITE_FAILED", error_message="SECRET password=/tmp/key",
    )
    with pytest.raises(ApiError) as raised:
        paper_router._action(result)
    assert raised.value.status_code == 500
    assert "SECRET" not in raised.value.message


def test_citation_dto_accepts_structured_object_and_nullable_raw_text():
    from types import SimpleNamespace
    response = paper_router.citations("paper", SimpleNamespace(
        bibliography_citations=lambda _: [SimpleNamespace(
            id="c1", paper_id="paper", source_format="bibtex", raw_text=None,
            structured_json={"title": "Study"}, parse_status="parsed",
            parse_warnings=[], is_selected=True,
        )]
    ))
    assert response[0].structured_json["title"] == "Study"
