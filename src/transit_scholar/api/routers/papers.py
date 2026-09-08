from __future__ import annotations

import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Query, UploadFile, status
from fastapi.responses import FileResponse

from transit_scholar.api.dependencies import get_product
from transit_scholar.api.errors import ApiError
from transit_scholar.api.schemas import (
    CitationResponse, DuplicateRelationListResponse, DuplicateRelationResponse,
    DuplicateResolutionRequest, DuplicateResolutionResponse, EnrichmentResponse,
    MetadataCandidateResponse, MetadataUpdateRequest, PaperActionResponse,
    PaperDetailResponse, PaperFileResponse, PaperImportResponse, PaperLibraryListResponse,
    PaperSummaryResponse,
)
from transit_scholar.config import settings
from transit_scholar.product.errors import ProductPayloadTooLargeError

router = APIRouter(prefix="/api/v1")


def _summary(row) -> PaperSummaryResponse:
    return PaperSummaryResponse.model_validate({
        "paper_id": row.paper_id, "title": row.title, "publication_year": row.publication_year,
        "venue": row.venue, "doi": row.doi, "arxiv_id": row.arxiv_id, "status": row.status,
        "primary_file_id": row.primary_file_id, "created_at": row.created_at,
        "updated_at": row.updated_at,
    })


def _action(result) -> PaperActionResponse:
    if result.error_code:
        code, status_code = _result_error_mapping(result.error_code)
        raise ApiError(
            code,
            result.error_message or "Paper operation failed",
            {"paper_id": result.paper_id},
            status_code,
        )
    return PaperActionResponse(paper_id=result.paper_id, status=result.status, updated_fields=result.updated_fields, audit_log_id=result.audit_log_id)


def _result_error_mapping(error_code: str) -> tuple[str, int]:
    """Translate frozen Layer 1 result codes to stable HTTP categories."""
    if error_code in {"PAPER_NOT_FOUND", "RELATION_NOT_FOUND", "FILE_NOT_FOUND"}:
        return "NOT_FOUND", 404
    if error_code in {"INVALID_STATE", "PAPER_IN_USE"}:
        return error_code, 409
    if error_code in {"INVALID_FIELDS", "INVALID_DECISION"}:
        return "VALIDATION_ERROR", 422
    return error_code, 500


@router.get("/papers", response_model=PaperLibraryListResponse)
def list_library_papers(status_filter: str | None = Query(None, alias="status"), include_deleted: bool = False, limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0), product=Depends(get_product)):
    return PaperLibraryListResponse(items=[_summary(row) for row in product.list_library_papers(status=status_filter, include_deleted=include_deleted, limit=limit, offset=offset)])


@router.post("/papers/import", response_model=PaperImportResponse, status_code=status.HTTP_201_CREATED)
def import_paper(file: UploadFile = File(...), product=Depends(get_product)):
    if file.content_type not in (None, "application/pdf"):
        raise ApiError("INVALID_FILE_TYPE", "Only PDF uploads are supported", {"content_type": file.content_type}, 422)
    root = Path("temp") / "api_uploads" / uuid.uuid4().hex
    root.mkdir(parents=True, exist_ok=True)
    target = root / (Path(file.filename or "upload.pdf").name or "upload.pdf")
    total = 0
    try:
        with target.open("wb") as output:
            while chunk := file.file.read(1024 * 1024):
                total += len(chunk)
                if total > settings.max_file_size_bytes:
                    raise ProductPayloadTooLargeError("PDF exceeds configured upload limit")
                output.write(chunk)
        result = product.import_paper(target)
        return PaperImportResponse.model_validate({k: getattr(result, k) for k in ("paper_id", "file_id", "status", "import_status", "metadata_status", "duplicate_status", "current_stage", "second_layer_ready", "second_layer_blockers", "error_code", "error_message")})
    finally:
        shutil.rmtree(root, ignore_errors=True)


@router.get("/papers/{paper_id}", response_model=PaperDetailResponse)
def paper_detail(paper_id: str, product=Depends(get_product)):
    row = product.read_paper(paper_id)
    if row is None:
        raise ApiError("NOT_FOUND", "Paper not found", {"paper_id": paper_id}, 404)
    data = {"paper_id": row.paper_id, "title": row.title, "publication_year": row.publication_year, "venue": row.venue, "doi": row.doi, "arxiv_id": row.arxiv_id, "status": row.status, "created_at": row.created_at, "updated_at": row.updated_at, "normalized_title": row.normalized_title, "abstract": row.abstract, "normalized_doi": row.normalized_doi, "authors": row.authors, "files": row.files, "duplicate_relations": row.duplicate_relations, "deleted_at": row.deleted_at}
    return PaperDetailResponse.model_validate(data)


@router.get("/papers/{paper_id}/second-layer")
def second_layer(paper_id: str, product=Depends(get_product)):
    result = product.read_second_layer_input(paper_id)
    if result is None or getattr(result, "error_code", None) == "paper_not_found":
        raise ApiError("NOT_FOUND", "Paper not found", {"paper_id": paper_id}, 404)
    data = result.__dict__.copy()
    data.pop("source_pdf_path", None)
    return data


@router.patch("/papers/{paper_id}/metadata", response_model=PaperActionResponse)
def update_metadata(paper_id: str, payload: MetadataUpdateRequest, product=Depends(get_product)):
    return _action(product.update_paper_metadata(paper_id, payload.supplied_fields()))


@router.post("/papers/{paper_id}/reconcile")
def reconcile(paper_id: str, product=Depends(get_product)):
    result = product.reconcile_paper(paper_id)
    if result.error_code == "PAPER_NOT_FOUND":
        raise ApiError("NOT_FOUND", "Paper not found", {"paper_id": paper_id}, 404)
    return {"paper_id": paper_id, "status": result.status, "second_layer_ready": result.second_layer_ready, "second_layer_blockers": result.second_layer_blockers, "error_code": result.error_code, "error_message": result.error_message}


@router.get("/papers/{paper_id}/metadata-candidates", response_model=list[MetadataCandidateResponse])
def metadata_candidates(paper_id: str, product=Depends(get_product)):
    return [MetadataCandidateResponse.model_validate({k: getattr(item, k) for k in ("id", "paper_id", "paper_file_id", "field_name", "value_text", "source_type", "source_location", "confidence", "is_selected")}) for item in product.metadata_candidates(paper_id)]


def _enrichment(result):
    return EnrichmentResponse.model_validate({"paper_id": result.paper_id, "doi": result.doi, "metadata_enrichment_status": result.status, "providers": [p.__dict__ for p in result.providers], "resolved": result.resolved, "error_code": result.error_code, "error_message": result.error_message})


@router.get("/papers/{paper_id}/enrichment", response_model=EnrichmentResponse)
def enrichment(paper_id: str, product=Depends(get_product)):
    result = product.enrichment(paper_id)
    if result is None:
        raise ApiError("NOT_FOUND", "Paper not found", {"paper_id": paper_id}, 404)
    return _enrichment(result)


@router.post("/papers/{paper_id}/enrichment/refresh", response_model=EnrichmentResponse)
def enrichment_refresh(paper_id: str, product=Depends(get_product)):
    result = product.refresh_enrichment(paper_id)
    if result.error_code == "paper_not_found":
        raise ApiError("NOT_FOUND", "Paper not found", {"paper_id": paper_id}, 404)
    return _enrichment(result)


@router.get("/papers/{paper_id}/duplicate-relations", response_model=DuplicateRelationListResponse)
def duplicate_relations(paper_id: str, product=Depends(get_product)):
    return DuplicateRelationListResponse(items=[DuplicateRelationResponse.model_validate(item.__dict__) for item in product.duplicate_relations(paper_id)])


@router.post("/duplicate-relations/{relation_id}/resolve", response_model=DuplicateResolutionResponse)
def resolve_relation(relation_id: str, payload: DuplicateResolutionRequest, product=Depends(get_product)):
    result = product.resolve_duplicate_relation(relation_id, payload.decision)
    if result.error_code:
        code, status_code = _result_error_mapping(result.error_code)
        raise ApiError(code, result.error_message or "Duplicate resolution failed", {"relation_id": relation_id}, status_code)
    return DuplicateResolutionResponse.model_validate(result.__dict__)


@router.get("/papers/{paper_id}/citations", response_model=list[CitationResponse])
def citations(paper_id: str, product=Depends(get_product)):
    return [CitationResponse.model_validate({k: getattr(item, k) for k in ("id", "paper_id", "source_format", "raw_text", "structured_json", "parse_status", "parse_warnings", "is_selected")}) for item in product.bibliography_citations(paper_id)]


@router.get("/papers/{paper_id}/files", response_model=list[PaperFileResponse])
def paper_files(paper_id: str, product=Depends(get_product)):
    if product.read_paper(paper_id) is None:
        raise ApiError("NOT_FOUND", "Paper not found", {"paper_id": paper_id}, 404)
    return [PaperFileResponse.model_validate(item) for item in product.list_registered_paper_files(paper_id)]


@router.get("/files/{file_id}/content")
def file_content(file_id: str, product=Depends(get_product)):
    registered = product.resolve_registered_pdf(file_id)
    if registered is None:
        raise ApiError("NOT_FOUND", "Registered PDF file not found", {"file_id": file_id}, 404)
    return FileResponse(registered.path, media_type=registered.content_type, filename=registered.filename)


@router.delete("/papers/{paper_id}", response_model=PaperActionResponse)
def delete_paper(paper_id: str, product=Depends(get_product)):
    from transit_scholar.product import PaperInUseError
    try:
        return _action(product.soft_delete_library_paper(paper_id))
    except PaperInUseError as exc:
        raise ApiError("PAPER_IN_USE", "Paper is still a member of an active Workspace", {"paper_id": str(exc)}, 409) from exc


@router.post("/papers/{paper_id}/restore", response_model=PaperActionResponse)
def restore(paper_id: str, product=Depends(get_product)):
    return _action(product.restore_library_paper(paper_id))
