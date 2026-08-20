"""Extraction manifest / run trace models (FR-B-009).

``FieldTraceEntry`` records everything the trace must answer per field:
query, retrieval status/method/error, hit chunk ids, candidate ids, LLM
structured output (safe summary), selected evidence ids, field result status,
and field-level error code/message. ``ExtractionManifest`` adds run-level
identity (run id, paper id, schema id/version/hash, LLM provider/model/fake,
created_at) and optional run-level errors. Both models are Pydantic and JSON
serializable via ``model_dump_json()``.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class FieldTraceEntry(BaseModel):
    """Per-field extraction trace (FR-B-009 / AC-L2S2B-11)."""

    field_id: str
    query: str = ""
    query_metadata: dict[str, Any] = Field(default_factory=dict)
    retrieval_status: str | None = None
    retrieval_method: str | None = None
    retrieval_error_code: str | None = None
    retrieval_error_message: str | None = None
    hit_chunk_ids: list[str] = Field(default_factory=list)
    candidate_ids: list[str] = Field(default_factory=list)
    llm_output: dict[str, Any] | None = None
    selected_evidence_ids: list[str] = Field(default_factory=list)
    field_result_status: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    retry_count: int = 0
    retry_feedback: str | None = None
    evidence_warnings: list[str] | None = None


class ExtractionManifest(BaseModel):
    """Run-level extraction manifest (FR-B-009 / AC-L2S2B-11)."""

    run_id: str
    paper_id: str
    schema_id: str
    schema_version: str
    schema_hash: str | None = None
    llm_provider: str = ""
    llm_model: str = ""
    llm_fake: bool = False
    created_at: str = ""
    run_error_code: str | None = None
    run_error_message: str | None = None
    fields: list[FieldTraceEntry] = Field(default_factory=list)
