"""Serialization helpers for the Web API.

Dataclasses and dataclass-like views (e.g. citation records) are converted to
plain dicts here so the FastAPI routes can return them directly. All
``datetime`` values are rendered as ISO8601 strings.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _to_dict_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def maintenance_item_to_dict(item: Any) -> dict[str, Any]:
    return {
        "item_id": item.item_id,
        "item_type": item.item_type,
        "severity": item.severity,
        "title": item.title,
        "description": item.description,
        "related_job_id": item.related_job_id,
        "related_paper_id": item.related_paper_id,
        "related_file_id": item.related_file_id,
        "paths": list(item.paths),
        "detected_at": _iso(item.detected_at),
        "can_purge": item.can_purge,
        "can_retry_import": item.can_retry_import,
        "can_manual_promote": item.can_manual_promote,
        "can_restore": item.can_restore,
        "requires_user_input": item.requires_user_input,
        "risk_level": item.risk_level,
        "recommended_actions": list(item.recommended_actions),
        "safe_actions": list(item.safe_actions),
        "dangerous_actions": list(item.dangerous_actions),
        "blockers": list(item.blockers),
    }


def maintenance_preview_to_dict(result: Any) -> dict[str, Any]:
    return {
        "item_id": result.item_id,
        "action": result.action,
        "allowed": result.allowed,
        "risk_level": result.risk_level,
        "requires_confirmation": result.requires_confirmation,
        "requires_user_input": result.requires_user_input,
        "affected_paths": list(result.affected_paths),
        "affected_db_records": list(result.affected_db_records),
        "will_delete_paths": list(result.will_delete_paths),
        "will_update_records": list(result.will_update_records),
        "will_create_records": list(result.will_create_records),
        "blockers": list(result.blockers),
        "message": result.message,
    }


def import_pipeline_result_to_dict(result: Any) -> dict[str, Any]:
    return {
        "status": result.status,
        "job_id": result.job_id,
        "paper_id": result.paper_id,
        "file_id": result.file_id,
        "is_exact_duplicate": result.is_exact_duplicate,
        "import_status": result.import_status,
        "metadata_status": result.metadata_status,
        "duplicate_status": result.duplicate_status,
        "relations_created": result.relations_created,
        "relations_existing": result.relations_existing,
        "relation_ids": list(result.relation_ids),
        "current_stage": result.current_stage,
        "error_code": result.error_code,
        "error_message": result.error_message,
        "warnings": list(result.warnings),
        "second_layer_ready": result.second_layer_ready,
        "second_layer_blockers": list(result.second_layer_blockers),
        "metadata_enrichment_status": result.metadata_enrichment_status,
        "enrichment_provider_results": [_to_dict_value(v) for v in result.enrichment_provider_results or []],
    }


def enrichment_to_dict(result: Any) -> dict[str, Any]:
    return {
        "paper_id": result.paper_id,
        "doi": result.doi,
        "metadata_enrichment_status": result.status,
        "providers": [
            {
                "provider": p.provider,
                "status": p.status,
                "http_status": p.http_status,
                "fetched_at": _iso(p.fetched_at),
                "attempt_count": p.attempt_count,
                "next_retry_at": _iso(p.next_retry_at),
                "error_code": p.error_code,
                "error_message": p.error_message,
                "fields": list(p.fields),
            }
            for p in result.providers
        ],
        "resolved": dict(result.resolved),
        "error_code": result.error_code,
        "error_message": result.error_message,
    }


def paper_summary_to_dict(result: Any) -> dict[str, Any]:
    return {
        "paper_id": result.paper_id,
        "title": result.title,
        "publication_year": result.publication_year,
        "venue": result.venue,
        "doi": result.doi,
        "arxiv_id": result.arxiv_id,
        "status": result.status,
        "primary_file_id": result.primary_file_id,
        "created_at": _iso(result.created_at),
        "updated_at": _iso(result.updated_at),
    }


def paper_detail_to_dict(result: Any) -> dict[str, Any]:
    return {
        "paper_id": result.paper_id,
        "title": result.title,
        "normalized_title": result.normalized_title,
        "abstract": result.abstract,
        "publication_year": result.publication_year,
        "venue": result.venue,
        "doi": result.doi,
        "normalized_doi": result.normalized_doi,
        "arxiv_id": result.arxiv_id,
        "status": result.status,
        "authors": [_to_dict_value(v) for v in result.authors],
        "files": [_to_dict_value(v) for v in result.files],
        "duplicate_relations": [_to_dict_value(v) for v in result.duplicate_relations],
        "created_at": _iso(result.created_at),
        "updated_at": _iso(result.updated_at),
        "deleted_at": _iso(result.deleted_at),
    }


def second_layer_to_dict(result: Any) -> dict[str, Any]:
    return {
        "status": result.status,
        "paper_id": result.paper_id,
        "primary_file_id": result.primary_file_id,
        "source_pdf_path": result.source_pdf_path,
        "relative_path": result.relative_path,
        "title": result.title,
        "authors": list(result.authors),
        "year": result.year,
        "doi": result.doi,
        "arxiv_id": result.arxiv_id,
        "page_count": result.page_count,
        "identity_status": result.identity_status,
        "duplicate_status": result.duplicate_status,
        "blockers": list(result.blockers),
        "error_code": result.error_code,
        "error_message": result.error_message,
    }


def metadata_candidate_to_dict(record: Any) -> dict[str, Any]:
    return {
        "id": record.id,
        "paper_id": record.paper_id,
        "paper_file_id": record.paper_file_id,
        "field_name": record.field_name,
        "value_text": record.value_text,
        "source_type": record.source_type,
        "source_location": record.source_location,
        "confidence": record.confidence,
        "is_selected": record.is_selected,
        "created_at": _iso(record.created_at),
    }


def audit_log_to_dict(record: Any) -> dict[str, Any]:
    return {
        "id": record.id,
        "entity_type": record.entity_type,
        "entity_id": record.entity_id,
        "action": record.action,
        "actor_type": record.actor_type,
        "old_value_json": record.old_value_json,
        "new_value_json": record.new_value_json,
        "created_at": _iso(record.created_at),
    }


def paper_trace_to_dict(result: Any) -> dict[str, Any]:
    fields = {
        k: {
            "field_name": v.field_name,
            "candidate_count": v.candidate_count,
            "selected": v.selected,
            "synced_to_paper": v.synced_to_paper,
            "top_confidence": v.top_confidence,
        }
        for k, v in result.metadata_summary.fields.items()
    }
    return {
        "paper_id": result.paper_id,
        "paper_status": result.paper_status,
        "primary_file_id": result.primary_file_id,
        "original_filename": result.original_filename,
        "sha256": result.sha256,
        "stored_relative_path": result.stored_relative_path,
        "stored_abs_path": result.stored_abs_path,
        "file_exists": result.file_exists,
        "ingestion_jobs": [
            {
                "job_id": j.job_id,
                "status": j.status,
                "current_stage": j.current_stage,
                "is_exact_duplicate": j.is_exact_duplicate,
            }
            for j in result.ingestion_jobs
        ],
        "metadata_summary": {
            "fields": fields,
            "total_candidates": result.metadata_summary.total_candidates,
            "selected_count": result.metadata_summary.selected_count,
        },
        "metadata_candidates": [_to_dict_value(v) for v in result.metadata_candidates],
        "duplicate_relations": [_to_dict_value(v) for v in result.duplicate_relations],
        "second_layer_gate": _to_dict_value(result.second_layer_gate),
        "steps": [
            {
                "step": s.step,
                "status": s.status,
                "records": list(s.records),
                "details": s.details,
                "paths": list(s.paths),
                "blockers": list(s.blockers),
            }
            for s in result.steps
        ],
        "error_code": result.error_code,
        "error_message": result.error_message,
    }


def citation_record_view_to_dict(view: Any) -> dict[str, Any]:
    return {
        "id": view.id,
        "paper_id": view.paper_id,
        "source_format": view.source_format,
        "raw_text": view.raw_text,
        "structured_json": view.structured_json,
        "parse_status": view.parse_status,
        "parse_warnings": list(view.parse_warnings),
        "is_selected": view.is_selected,
        "created_at": _iso(view.created_at),
        "updated_at": _iso(view.updated_at),
        "deleted_at": _iso(view.deleted_at),
    }
