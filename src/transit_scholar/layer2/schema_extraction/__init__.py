"""L2S2 Schema core (Package A), extraction engine (Package B), and
validation + recheck layer (Package C).

Public contract (stable import path ``transit_scholar.layer2.schema_extraction``):

- Package A models: ``SchemaDefinition``, ``SectionDefinition``,
  ``FieldDefinition``, ``FieldResult``, ``EvidenceRef``, ``SchemaInstance``,
  ``ValidationIssue``;
- Package A functions: ``list_schema_plugins()``,
  ``get_schema_definition(schema_id)``,
  ``validate_schema_instance(definition, instance)``,
  ``compute_schema_hash(definition)``;
- Package B (in-memory extraction, FR-B-001..011): ``LLMConfig``,
  ``FakeLLMProvider``, ``RealLLMClientStub``, ``OpenAICompatibleLLMClient``,
  ``resolve_llm_client``, ``resolve_runtime_llm_client``,
  ``build_field_query``, ``FakeRetrieval``,
  ``HybridRetrievalWrapper``, ``CandidateEvidence``, ``map_hits_to_candidates``,
  ``bind_evidence``, ``FieldTraceEntry``, ``ExtractionManifest``,
  ``ExtractionEngine``, ``ExtractionRun``,
  ``extract_schema_instance_in_memory(...)`` and the Package B error types.
- Package C (in-memory validation + recheck, FR-C-001..008):
  ``ValidationReport``, ``ReportStatus``, ``derive_report_status``,
  ``validate_evidence_integrity``, ``CanonicalReader``,
  ``CanonicalReadError``, ``SemanticVerdict``, ``SemanticDecision``,
  ``SemanticVerifier``, ``FakeSemanticVerifier``,
  ``StructuredSemanticVerifier``, ``build_semantic_verifier_messages``,
  ``VerifierUnavailableError``, ``verify_field_semantics``,
  ``RecheckTrace``, ``RecheckTraceEntry``, ``RecheckCallable``,
  ``RecheckError``, ``run_targeted_recheck``,
  ``validate_schema_instance_in_memory(...)``,
  ``run_validation_pipeline_in_memory``.
- Package D (persistence + versioning + public API):
  ``extract_schema``, ``get_schema``, ``get_field``, ``validate_schema``,
  ``recheck_fields``, ``schema_enabled``, ``SchemaRunResult``,
  ``SchemaRunStorage``, ``StoredRun``, ``RunManifest``, ``CurrentPointer``,
  ``FileDigest``, ``PROMPT_VERSION``, ``compute_extraction_config_hash``,
  and the Package D error types.

This package is self-contained: it imports only stdlib, ``pydantic`` and
``yaml`` at module import time. No LLM, network, database, parser, retrieval,
or L2S1 module is imported (L2S1 types are only referenced lazily inside
methods or under ``TYPE_CHECKING``). ``transit_scholar.config`` is imported
lazily by ``resolve_runtime_llm_client`` (single dotenv bootstrap boundary)
and by the persistence layer when a default storage root is resolved.
"""

from __future__ import annotations

from .api import (
    SchemaExtractionRunError,
    SchemaFieldMissingError,
    SchemaFieldNotFoundError,
    SchemaIdMismatchError,
    SchemaRecheckError,
    SchemaRunResult,
    extract_schema,
    get_field,
    get_schema,
    recheck_fields,
    schema_enabled,
    validate_schema,
)
from .engine import (
    FieldExtractionLLMOutput,
    ExtractionEngine,
    ExtractionRun,
    build_extraction_messages,
    build_runtime_recheck_callable,
    extract_field_instance_in_memory,
    extract_schema_instance_in_memory,
)
from .errors import (
    EvidenceBindingError,
    LLMCapabilityError,
    LLMInvalidOutputError,
    LLMRequestError,
    LLMUnavailableError,
    RetrievalUnavailableError,
    SchemaExtractionError,
    SchemaLoadError,
    UnknownEvidenceIdError,
)
from .evidence import (
    CandidateEvidence,
    SourceRefRecord,
    bind_evidence,
    enrich_candidates_with_blocks,
    map_hits_to_candidates,
)
from .evidence_validation import (
    CanonicalReadError,
    CanonicalReader,
    validate_evidence_integrity,
)
from .hashing import compute_schema_hash
from .llm import (
    FakeCallRecord,
    FakeLLMProvider,
    LLMConfig,
    OpenAICompatibleLLMClient,
    RealLLMClientStub,
    StructuredLLMClient,
    StructuredOutputMode,
    resolve_llm_client,
    resolve_runtime_llm_client,
)
from .loader import (
    InvalidSchemaDefinitionError,
    SchemaPluginNotFoundError,
    get_schema_definition,
    list_schema_plugins,
)
from .models import (
    FIELD_STATUSES,
    FIELD_TYPES,
    EvidenceRef,
    FieldDefinition,
    FieldResult,
    SchemaDefinition,
    SchemaInstance,
    SectionDefinition,
    ValidationIssue,
)
from .persistence import (
    PROMPT_VERSION,
    CurrentPointer,
    FileDigest,
    RunManifest,
    SchemaCorruptRunError,
    SchemaCurrentNotFoundError,
    SchemaFileMissingError,
    SchemaHashMismatchError,
    SchemaInvalidJsonError,
    SchemaRunIdMismatchError,
    SchemaRunNotFoundError,
    SchemaRunStorage,
    SchemaStorageError,
    StoredRun,
    compute_extraction_config_hash,
)
from .query import FieldQuery, build_field_query
from .recheck import (
    RecheckCallable,
    RecheckError,
    RecheckTrace,
    RecheckTraceEntry,
    run_targeted_recheck,
)
from .retrieval import FakeRetrieval, HybridRetrievalWrapper, RetrievalBoundary
from .semantic import (
    FakeSemanticVerifier,
    SemanticDecision,
    SemanticVerdict,
    SemanticVerifier,
    StructuredSemanticVerifier,
    VerifierUnavailableError,
    build_semantic_verifier_messages,
    verify_field_semantics,
)
from .trace import ExtractionManifest, FieldTraceEntry
from .validation import validate_schema_instance
from .validation_pipeline import (
    CrossFieldValidator,
    run_validation_pipeline_in_memory,
    validate_schema_instance_in_memory,
)
from .validation_report import (
    ReportStatus,
    ValidationReport,
    derive_report_status,
)

__all__ = [
    "SchemaDefinition",
    "SectionDefinition",
    "FieldDefinition",
    "FieldResult",
    "EvidenceRef",
    "SchemaInstance",
    "ValidationIssue",
    "list_schema_plugins",
    "get_schema_definition",
    "validate_schema_instance",
    "compute_schema_hash",
    "SchemaPluginNotFoundError",
    "InvalidSchemaDefinitionError",
    "FIELD_TYPES",
    "FIELD_STATUSES",
    "SchemaExtractionError",
    "SchemaLoadError",
    "RetrievalUnavailableError",
    "LLMUnavailableError",
    "LLMCapabilityError",
    "LLMInvalidOutputError",
    "LLMRequestError",
    "UnknownEvidenceIdError",
    "EvidenceBindingError",
    "LLMConfig",
    "StructuredLLMClient",
    "StructuredOutputMode",
    "FakeCallRecord",
    "FakeLLMProvider",
    "OpenAICompatibleLLMClient",
    "RealLLMClientStub",
    "resolve_llm_client",
    "resolve_runtime_llm_client",
    "FieldQuery",
    "build_field_query",
    "RetrievalBoundary",
    "FakeRetrieval",
    "HybridRetrievalWrapper",
    "SourceRefRecord",
    "CandidateEvidence",
    "map_hits_to_candidates",
    "enrich_candidates_with_blocks",
    "bind_evidence",
    "FieldTraceEntry",
    "ExtractionManifest",
    "FieldExtractionLLMOutput",
    "build_extraction_messages",
    "ExtractionEngine",
    "ExtractionRun",
    "build_runtime_recheck_callable",
    "extract_field_instance_in_memory",
    "extract_schema_instance_in_memory",
    "ValidationReport",
    "ReportStatus",
    "derive_report_status",
    "CanonicalReader",
    "CanonicalReadError",
    "validate_evidence_integrity",
    "SemanticDecision",
    "SemanticVerdict",
    "SemanticVerifier",
    "FakeSemanticVerifier",
    "StructuredSemanticVerifier",
    "build_semantic_verifier_messages",
    "VerifierUnavailableError",
    "verify_field_semantics",
    "RecheckCallable",
    "RecheckError",
    "RecheckTrace",
    "RecheckTraceEntry",
    "run_targeted_recheck",
    "CrossFieldValidator",
    "validate_schema_instance_in_memory",
    "run_validation_pipeline_in_memory",
    # Package D: persistence / versioning / public API
    "extract_schema",
    "get_schema",
    "get_field",
    "validate_schema",
    "recheck_fields",
    "schema_enabled",
    "SchemaRunResult",
    "SchemaRunStorage",
    "StoredRun",
    "RunManifest",
    "CurrentPointer",
    "FileDigest",
    "PROMPT_VERSION",
    "compute_extraction_config_hash",
    "SchemaStorageError",
    "SchemaRunNotFoundError",
    "SchemaCurrentNotFoundError",
    "SchemaFileMissingError",
    "SchemaInvalidJsonError",
    "SchemaHashMismatchError",
    "SchemaRunIdMismatchError",
    "SchemaCorruptRunError",
    "SchemaExtractionRunError",
    "SchemaIdMismatchError",
    "SchemaFieldNotFoundError",
    "SchemaFieldMissingError",
    "SchemaRecheckError",
]
