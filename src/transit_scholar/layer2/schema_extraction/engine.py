"""L2S2 extraction engine (FR-B-007/008/009/010).

Orchestrates the in-memory pipeline per field:

``field -> build_field_query -> retrieval boundary -> map_hits_to_candidates
(E1..En) -> [canonical block enrichment] -> structured LLM output -> field-type
enforcement -> evidence binding -> FieldResult``

and assembles a Package A ``SchemaInstance`` plus an ``ExtractionManifest``
run trace.

Field completeness guarantee (FR-001 / AC-T001-F01..F04): a failing field is
never dropped. Every field-level failure (``retrieval_unavailable``,
``llm_unavailable``, ``llm_invalid_output`` after retry, ``unknown_evidence_id``
after retry, ``evidence_binding_failed`` after retry) yields an explicit
placeholder ``FieldResult(value=None, status="unclear", evidence=[])`` whose
``notes`` records the failure type, while the manifest keeps the original
``error_code`` / ``error_message`` / raw-output summary. ``not_found`` stays
reserved for runs where the pipeline executed normally and found no value.
Schema load failures are run-level (``run_error_code="schema_load_failed"``)
and keep ``instance=None``.

Targeted retry (FR-002 / AC-T001-F06..F09): at most one corrective retry per
retriable field error (invalid value type, absent status carrying evidence,
unknown evidence id, evidence binding failure, invalid structured output). A
second failure falls back to the placeholder — never dropped, never a
fabricated ``not_found``.

This module imports only within ``schema_extraction`` (plus stdlib/pydantic):
no L2S1 retrieval, parser, chunker, normalizer, config, or DB module is
imported at module import time.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, Field

from .errors import (
    LLMInvalidOutputError,
    LLMUnavailableError,
    EvidenceBindingError,
    UnknownEvidenceIdError,
)
from .evidence import (
    CandidateEvidence,
    bind_evidence,
    enrich_candidates_with_blocks,
    map_hits_to_candidates,
)
from .hashing import compute_schema_hash
from .llm import FakeLLMProvider, LLMConfig, StructuredLLMClient
from .loader import (
    InvalidSchemaDefinitionError,
    SchemaPluginNotFoundError,
    get_schema_definition,
)
from .models import (
    FieldDefinition,
    FieldResult,
    FieldStatus,
    SchemaDefinition,
    SchemaInstance,
    SectionDefinition,
    value_matches_field_type,
)
from .query import FieldQuery, build_field_query
from .retrieval import FakeRetrieval, RetrievalBoundary
from .trace import ExtractionManifest, FieldTraceEntry

#: Error code vocabulary (FR-B-010 / AC-L2S2B-12).
ERROR_RETRIEVAL_UNAVAILABLE = "retrieval_unavailable"
ERROR_LLM_UNAVAILABLE = "llm_unavailable"
ERROR_LLM_INVALID_OUTPUT = "llm_invalid_output"
ERROR_UNKNOWN_EVIDENCE_ID = "unknown_evidence_id"
ERROR_EVIDENCE_BINDING_FAILED = "evidence_binding_failed"
ERROR_SCHEMA_LOAD_FAILED = "schema_load_failed"

#: Frozen field status semantics (AC-T001-F14(c), verbatim-intent). This is
#: the single code copy referenced by tests; the same 口径 is mirrored in the
#: extraction prompt, ``schema.yaml`` ``status_semantics``, and the doc.
STATUS_SEMANTICS: dict[str, str] = {
    "explicit": "原文存在直接陈述、直接等价术语、表格/图注/实验设置中的直接事实。",
    "inferred": "需要跨句归纳、领域分类映射、由多个事实综合推出。",
    "not_found": "候选证据中没有足够支持。",
    "not_applicable": "该字段对论文研究对象不适用。",
    "unclear": "候选证据不足、LLM 输出矛盾、或系统无法安全判定。",
    "conflicting": "候选证据存在相互矛盾信息。",
}

#: Per-``FieldDefinition.type`` structural output guidance embedded in the
#: extraction prompt (AC-T001-F08). Natural-language paragraphs are explicitly
#: prohibited inside object/list values.
FIELD_TYPE_GUIDANCE: dict[str, str] = {
    "string": "value must be a JSON string or null; never a number, boolean, object or array.",
    "number": "value must be a JSON number (int/float) or null; never a boolean, string, object or array.",
    "boolean": "value must be a JSON boolean true/false or null; never a number, string, object or array.",
    "enum": "value must be exactly one of the listed options, or null.",
    "list": "value must be a JSON array, never a string, object or number. Do not put natural-language paragraphs inside the array.",
    "object": "value must be a JSON object, never a string, array or number. Do not put natural-language paragraphs inside the object.",
}


class FieldExtractionLLMOutput(BaseModel):
    """LLM structured output contract (FR-B-002, AC-L2S2B-04).

    The LLM may only produce ``value``, ``status``, ``evidence_ids``,
    ``confidence`` and ``notes``. Any provenance field (``block_id``,
    ``char_start``, ``char_end``, ``pages``, ``section_path``, ``quote``) is
    rejected by ``extra="forbid"``.
    """

    model_config = ConfigDict(extra="forbid")

    value: Any = None
    status: FieldStatus
    evidence_ids: list[str] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0, le=1)
    notes: str | None = None


def _json_safe(value: Any) -> Any:
    try:
        json.dumps(value)
    except (TypeError, ValueError):
        return repr(value)
    return value


def _is_empty_value(value: Any) -> bool:
    """True when ``value`` carries no assertive content (None, empty string,
    empty list/dict). Empty containers are tolerated alongside an absent
    status; anything else is treated as an assertive value."""
    if value is None:
        return True
    if isinstance(value, (list, dict, tuple, set, str)):
        return len(value) == 0
    return False


def _llm_output_summary(output: FieldExtractionLLMOutput) -> dict[str, Any]:
    return {key: _json_safe(value) for key, value in output.model_dump().items()}


def _render_output_constraints(field: FieldDefinition) -> dict[str, Any]:
    """Deterministic per-field structural guidance derived from the field type
    (AC-T001-F08). Includes the type constraint, the enum options when
    relevant, any documented complex-object skeleton, and any schema-level
    ``value_guidance`` text (generic additive guidance, all field types)."""
    constraints: dict[str, Any] = {
        "type": field.type,
        "constraint": FIELD_TYPE_GUIDANCE.get(field.type, ""),
    }
    if field.type == "enum":
        constraints["options"] = list(field.options or [])
    if field.output_guidance:
        skeleton = field.output_guidance.get("skeleton")
        if field.type == "object" and isinstance(skeleton, dict):
            constraints["object_skeleton"] = skeleton
        value_guidance = field.output_guidance.get("value_guidance")
        if isinstance(value_guidance, str) and value_guidance.strip():
            constraints["value_guidance"] = value_guidance.strip()
    return constraints


def _retry_feedback_text(
    field: FieldDefinition,
    category: str,
    reason: str,
    candidates: list[CandidateEvidence],
) -> str:
    """Build the corrective feedback for one targeted retry (AC-T001-F06)."""
    known_ids = ", ".join(c.evidence_id for c in candidates)
    if category == ERROR_UNKNOWN_EVIDENCE_ID:
        return (
            "Retry: the previous answer selected an unknown evidence_id. "
            f"Only choose evidence_ids from the known candidate ids: {known_ids}. "
            f"Detail: {reason}"
        )
    if category == ERROR_EVIDENCE_BINDING_FAILED:
        return (
            "Retry: the selected evidence could not be bound to canonical "
            f"blocks. Re-check evidence_ids against the known candidate ids: "
            f"{known_ids}. Detail: {reason}"
        )
    if category == ERROR_LLM_INVALID_OUTPUT:
        if "does not match field type" in reason:
            return (
                "Retry: the previous value does not match the required field "
                f"type {field.type!r}. Follow the output_constraints exactly "
                f"(never use natural-language paragraphs inside object/list "
                f"values, never return a string where an object/array/boolean/"
                f"number is required). Detail: {reason}"
            )
        if "must not carry a non-null value" in reason:
            return (
                "Retry: absent statuses (not_found / not_applicable) must "
                "have value null (no placeholder strings like 'unspecified' "
                "or 'not stated'). Return value: null with evidence_ids: [] "
                f"when the fact is absent. Detail: {reason}"
            )
        if "must not carry" in reason:
            return (
                "Retry: absent statuses (not_found / not_applicable) must NOT "
                "carry any evidence_ids. Return evidence_ids: [] for absent "
                f"fields. Detail: {reason}"
            )
        return (
            "Retry: the previous structured output was invalid. Return exactly "
            "a JSON object with only value/status/evidence_ids/confidence/"
            "notes, never provenance fields (block_id/char_start/char_end/"
            f"quote/pages/section_path). Detail: {reason}"
        )
    return f"Retry with corrections. Detail: {reason}"


def build_extraction_messages(
    field: FieldDefinition,
    section: SectionDefinition,
    definition: SchemaDefinition,
    query: str,
    candidates: list[CandidateEvidence],
    *,
    retry_feedback: str | None = None,
) -> list[dict[str, Any]]:
    """Deterministic prompt construction for one field extraction.

    Carries: the frozen status semantics (AC-T001-F14), per-field structural
    output constraints from the field type (AC-T001-F08), the candidate
    evidence list, and an optional corrective ``retry_feedback``.
    """
    system = (
        "You are a structured schema extraction assistant. Extract a value "
        "for exactly one schema field from the provided candidate evidence. "
        "Answer as a JSON object with the allowed structured fields only: value, status, "
        "evidence_ids, confidence, notes. Never produce provenance fields. "
        "Never choose an absent status (not_found / not_applicable) together "
        "with evidence_ids. "
        "A fact stated directly in the paper text — including statements in "
        "the experimental settings, table cells and figure captions — counts "
        "as status 'explicit'; use 'inferred' only when the value must be "
        "synthesized across sentences or mapped to a domain category. Report "
        "qualitative findings (comparative claims, conditions, trade-offs) as "
        "valid values whenever the evidence supports them, even when concrete "
        "numeric figures are absent; choose 'not_found' only when the "
        "candidate evidence contains no support at all for the requested "
        "field."
    )
    prompt: dict[str, Any] = {
        "task": "extract_field",
        "schema_id": definition.schema_id,
        "schema_version": definition.version,
        "section_id": section.id,
        "section_label": section.label,
        "field": {
            "id": field.id,
            "label": field.label,
            "question": field.question,
            "description": field.description,
            "type": field.type,
            "options": field.options,
            "evidence_required": field.evidence_required,
            "allow_inference": field.allow_inference,
        },
        "output_constraints": _render_output_constraints(field),
        "status_semantics": dict(STATUS_SEMANTICS),
        "retrieval_query": query,
        "candidate_evidence": [
            {
                "id": candidate.evidence_id,
                "rank": candidate.rank,
                "method": candidate.method,
                "score": candidate.score,
                "pages": candidate.pages,
                "section_path": candidate.section_path,
                "text": candidate.text,
            }
            for candidate in candidates
        ],
        "output_schema": {
            "value": "extracted value matching output_constraints.type",
            "status": "explicit|inferred|unclear|not_found|not_applicable|conflicting",
            "evidence_ids": ["E1", "E2", "..."],
            "confidence": "0..1 or null",
            "notes": "optional note",
        },
    }
    if retry_feedback:
        prompt["retry_feedback"] = retry_feedback
    return [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": json.dumps(prompt, ensure_ascii=False, indent=2),
        },
    ]


def _collect_block_ids(candidates: list[CandidateEvidence]) -> list[str]:
    seen: list[str] = []
    for candidate in candidates:
        for ref in candidate.source_refs:
            if ref.block_id not in seen:
                seen.append(ref.block_id)
    return seen


def _normalize_blocks(blocks: Any) -> dict[str, dict[str, Any]]:
    """Normalize a canonical reader result (dict or L2S1 list) to a
    ``block_id -> block`` mapping."""
    if isinstance(blocks, dict):
        return blocks
    if isinstance(blocks, list):
        return {
            block["block_id"]: block
            for block in blocks
            if isinstance(block, dict) and isinstance(block.get("block_id"), str)
        }
    return {}


class ExtractionRun(BaseModel):
    """In-memory run result: instance plus manifest (FR-B-008/009)."""

    instance: SchemaInstance | None = None
    manifest: ExtractionManifest


class ExtractionEngine:
    """In-memory schema extraction engine (FR-B-008).

    Defaults are fully offline: ``FakeLLMProvider`` and ``FakeRetrieval``,
    with no canonical reader (so no canonical block read ever happens without
    explicit injection).
    """

    def __init__(
        self,
        llm_client: StructuredLLMClient | None = None,
        retrieval: RetrievalBoundary | None = None,
        top_k: int = 8,
        llm_config: LLMConfig | None = None,
        canonical_reader: Callable[[str, list[str]], Any] | None = None,
    ):
        self.llm_config = llm_config or LLMConfig.from_env()
        self.llm_client = llm_client if llm_client is not None else FakeLLMProvider()
        self.retrieval = retrieval if retrieval is not None else FakeRetrieval()
        self.top_k = int(top_k)
        self.canonical_reader = canonical_reader

    # ------------------------------------------------------------------
    # run
    # ------------------------------------------------------------------

    def run(
        self,
        paper_id: str,
        schema_id: str | None = None,
        *,
        definition: SchemaDefinition | None = None,
        run_id: str | None = None,
    ) -> ExtractionRun:
        """Execute extraction for every field of the schema (in definition
        order) and return the in-memory instance plus manifest.

        Every field id of the definition is always present in
        ``instance.fields`` (AC-T001-F01): failing fields carry an ``unclear``
        placeholder instead of disappearing.
        """
        if definition is None:
            if not schema_id:
                raise ValueError("schema_id is required when no definition is provided")
            try:
                definition = get_schema_definition(schema_id)
            except (SchemaPluginNotFoundError, InvalidSchemaDefinitionError) as exc:
                return self._run_error(
                    paper_id,
                    schema_id,
                    run_id,
                    ERROR_SCHEMA_LOAD_FAILED,
                    f"failed to load schema definition {schema_id!r}: {exc}",
                )
        provider, model, fake = self._llm_identity()
        manifest = ExtractionManifest(
            run_id=run_id or uuid.uuid4().hex,
            paper_id=paper_id,
            schema_id=definition.schema_id,
            schema_version=definition.version,
            schema_hash=compute_schema_hash(definition),
            llm_provider=provider,
            llm_model=model,
            llm_fake=fake,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        fields: dict[str, FieldResult] = {}
        for section in definition.sections:
            for field in section.fields:
                result, entry = self._extract_field(paper_id, definition, section, field)
                manifest.fields.append(entry)
                fields[field.id] = result
        instance = SchemaInstance(
            paper_id=paper_id,
            schema_id=definition.schema_id,
            schema_version=definition.version,
            fields=fields,
        )
        return ExtractionRun(instance=instance, manifest=manifest)

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _llm_identity(self) -> tuple[str, str, bool]:
        client = self.llm_client
        provider = getattr(client, "provider_name", None) or self.llm_config.provider
        model = getattr(client, "model_name", None) or self.llm_config.model
        return (
            provider or "unknown",
            model or "unknown",
            bool(getattr(client, "is_fake", False)),
        )

    def _run_error(
        self,
        paper_id: str,
        schema_id: str | None,
        run_id: str | None,
        error_code: str,
        message: str,
    ) -> ExtractionRun:
        provider, model, fake = self._llm_identity()
        manifest = ExtractionManifest(
            run_id=run_id or uuid.uuid4().hex,
            paper_id=paper_id,
            schema_id=schema_id or "",
            schema_version="",
            schema_hash=None,
            llm_provider=provider,
            llm_model=model,
            llm_fake=fake,
            created_at=datetime.now(timezone.utc).isoformat(),
            run_error_code=error_code,
            run_error_message=message,
        )
        return ExtractionRun(instance=None, manifest=manifest)

    def _placeholder(self, entry: FieldTraceEntry) -> FieldResult:
        """Explicit ``unclear`` placeholder for a failed field (FR-001).

        The ``notes`` records the stable failure type plus a one-line reason;
        the manifest entry keeps ``error_code`` / ``error_message`` (and the
        raw-output summary) so the placeholder never erases the trace.
        """
        entry.field_result_status = "unclear"
        code = entry.error_code or "unclear"
        reason = entry.error_message or "field could not be extracted safely"
        return FieldResult(
            value=None,
            status="unclear",
            evidence=[],
            confidence=None,
            notes=f"unclear: {code}: {reason[:200]}",
        )

    def _enrich_candidates(
        self, paper_id: str, candidates: list[CandidateEvidence]
    ) -> str | None:
        """Fill per-ref canonical provenance from the injected reader.

        Returns an error message when the reader raises (no fabrication; the
        engine then falls back to the evidence-binding failure path).
        """
        if self.canonical_reader is None:
            return None
        block_ids = _collect_block_ids(candidates)
        if not block_ids:
            return None
        try:
            blocks = self.canonical_reader(paper_id, block_ids)
        except Exception as exc:  # noqa: BLE001 - explicit system failure
            return f"canonical reader raised {type(exc).__name__}: {exc}"
        enrich_candidates_with_blocks(candidates, _normalize_blocks(blocks))
        return None

    def _extract_field(
        self,
        paper_id: str,
        definition: SchemaDefinition,
        section: SectionDefinition,
        field: FieldDefinition,
    ) -> tuple[FieldResult, FieldTraceEntry]:
        entry = FieldTraceEntry(field_id=field.id)
        field_query: FieldQuery = build_field_query(field, section, definition)
        entry.query = field_query.query
        entry.query_metadata = dict(field_query.metadata)

        try:
            result = self.retrieval.retrieve(paper_id, field_query.query, self.top_k)
        except Exception as exc:  # noqa: BLE001 - boundary failure is explicit
            entry.retrieval_status = "unavailable"
            entry.retrieval_error_code = "boundary_exception"
            entry.retrieval_error_message = f"{type(exc).__name__}: {exc}"
            entry.error_code = ERROR_RETRIEVAL_UNAVAILABLE
            entry.error_message = (
                f"retrieval boundary raised for field {field.id!r}: {exc}"
            )
            return self._placeholder(entry), entry

        entry.retrieval_status = getattr(result, "status", "unknown")
        entry.retrieval_method = getattr(result, "method", "unknown")
        if entry.retrieval_status == "unavailable":
            entry.retrieval_error_code = getattr(result, "error_code", None)
            entry.retrieval_error_message = getattr(result, "error_message", None)
            entry.error_code = ERROR_RETRIEVAL_UNAVAILABLE
            entry.error_message = (
                f"retrieval unavailable for field {field.id!r}: "
                f"{entry.retrieval_error_message or entry.retrieval_error_code or 'no detail'}"
            )
            return self._placeholder(entry), entry

        hits = list(getattr(result, "hits", []) or [])
        entry.hit_chunk_ids = [
            hit.chunk_id for hit in hits if getattr(hit, "chunk_id", None) is not None
        ]

        if not hits:
            entry.field_result_status = "not_found"
            return FieldResult(value=None, status="not_found"), entry

        candidates = map_hits_to_candidates(hits)
        entry.candidate_ids = [candidate.evidence_id for candidate in candidates]

        enrichment_error = self._enrich_candidates(paper_id, candidates)
        if enrichment_error is not None:
            entry.error_code = ERROR_EVIDENCE_BINDING_FAILED
            entry.error_message = (
                f"field {field.id!r}: canonical enrichment failed: {enrichment_error}"
            )
            return self._placeholder(entry), entry

        allow_candidate_fallback = self.canonical_reader is None

        # -- bounded attempt loop: first attempt + at most one retry ---------
        final_output: FieldExtractionLLMOutput | None = None
        final_evidence_refs: list = []
        failure_category: str | None = None
        failure_reason: str | None = None
        retry_feedback: str | None = None
        attempt = 0
        while attempt <= 1:
            messages = build_extraction_messages(
                field,
                section,
                definition,
                field_query.query,
                candidates,
                retry_feedback=retry_feedback,
            )
            metadata = {"field_id": field.id, "prompt_key": field.id}
            attempt_output: FieldExtractionLLMOutput | None = None
            evidence_refs: list | None = None
            category: str | None = None
            reason: str | None = None
            try:
                attempt_output = self.llm_client.generate_structured(
                    messages, FieldExtractionLLMOutput, metadata
                )
            except LLMInvalidOutputError as exc:
                category = ERROR_LLM_INVALID_OUTPUT
                reason = f"field {field.id!r}: {exc}"
            except LLMUnavailableError as exc:
                category = ERROR_LLM_UNAVAILABLE
                reason = f"field {field.id!r}: {exc}"
            except Exception as exc:  # noqa: BLE001 - LLM client failure is explicit
                category = ERROR_LLM_UNAVAILABLE
                reason = (
                    f"field {field.id!r}: LLM client failure {type(exc).__name__}: {exc}"
                )

            if attempt_output is not None:
                entry.llm_output = _llm_output_summary(attempt_output)
                entry.selected_evidence_ids = list(attempt_output.evidence_ids)
                if not value_matches_field_type(
                    field.type, attempt_output.value, field.options
                ):
                    category = ERROR_LLM_INVALID_OUTPUT
                    reason = (
                        f"field {field.id!r}: LLM value "
                        f"{_json_safe(attempt_output.value)!r} does not match "
                        f"field type {field.type!r}"
                    )
                elif (
                    attempt_output.status in ("not_found", "not_applicable")
                    and attempt_output.evidence_ids
                ):
                    category = ERROR_LLM_INVALID_OUTPUT
                    reason = (
                        f"field {field.id!r}: status {attempt_output.status!r} "
                        "must not carry evidence_ids"
                    )
                elif (
                    attempt_output.status in ("not_found", "not_applicable")
                    and not _is_empty_value(attempt_output.value)
                ):
                    category = ERROR_LLM_INVALID_OUTPUT
                    reason = (
                        f"field {field.id!r}: status {attempt_output.status!r} "
                        "must not carry a non-null value"
                    )
                elif attempt_output.status in ("not_found", "not_applicable"):
                    final_output = attempt_output
                    final_evidence_refs = []
                    category = None
                    reason = None
                else:
                    evidence_warnings: list[str] = []
                    try:
                        evidence_refs = bind_evidence(
                            attempt_output.evidence_ids,
                            candidates,
                            field_id=field.id,
                            allow_candidate_fallback=allow_candidate_fallback,
                            warnings_out=evidence_warnings,
                        )
                    except UnknownEvidenceIdError as exc:
                        category = ERROR_UNKNOWN_EVIDENCE_ID
                        reason = f"field {field.id!r}: {exc}"
                    except EvidenceBindingError as exc:
                        category = ERROR_EVIDENCE_BINDING_FAILED
                        reason = f"field {field.id!r}: {exc}"
                    else:
                        entry.evidence_warnings = (
                            list(evidence_warnings) if evidence_warnings else None
                        )
                        final_output = attempt_output
                        final_evidence_refs = evidence_refs
                        category = None
                        reason = None

            if category is None:
                break  # success

            if category == ERROR_LLM_UNAVAILABLE:
                # system failure: not retriable
                failure_category = category
                failure_reason = reason
                final_output = None
                break

            if attempt < 1:
                # one targeted retry with corrective feedback
                retry_feedback = _retry_feedback_text(
                    field, category, reason, candidates
                )
                entry.retry_count += 1
                entry.retry_feedback = retry_feedback
                attempt += 1
                continue

            # retry exhausted: fall back to the placeholder (never dropped,
            # never a fabricated not_found)
            failure_category = category
            failure_reason = reason
            final_output = None
            break

        if final_output is not None:
            field_result = FieldResult(
                value=final_output.value,
                status=final_output.status,
                evidence=final_evidence_refs,
                confidence=final_output.confidence,
                notes=final_output.notes,
            )
            entry.field_result_status = final_output.status
            return field_result, entry

        entry.error_code = failure_category or ERROR_LLM_INVALID_OUTPUT
        entry.error_message = failure_reason or "field extraction failed"
        return self._placeholder(entry), entry


def extract_schema_instance_in_memory(
    paper_id: str,
    schema_id: str,
    *,
    llm_client: StructuredLLMClient | None = None,
    retrieval: RetrievalBoundary | None = None,
    top_k: int = 8,
    run_id: str | None = None,
    canonical_reader: Callable[[str, list[str]], Any] | None = None,
) -> ExtractionRun:
    """In-memory entry function (FR-B-011 / AC-L2S2B-13).

    Loads the schema definition from the plugin loader (run-level
    ``schema_load_failed`` marker when it cannot be loaded), then runs the
    extraction engine with offline defaults. No persistence. An injectable
    ``canonical_reader`` supplies per-ref canonical provenance for evidence
    binding; without it the offline candidate fallback is used.
    """
    engine = ExtractionEngine(
        llm_client=llm_client,
        retrieval=retrieval,
        top_k=top_k,
        canonical_reader=canonical_reader,
    )
    return engine.run(paper_id, schema_id, run_id=run_id)
