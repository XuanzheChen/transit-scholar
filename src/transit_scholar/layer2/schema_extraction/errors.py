"""L2S2 Package B error model (FR-B-010).

Every error carries a stable machine-readable ``error_code`` so the engine can
record it in the extraction manifest / run trace. Error classes are grouped by
stage: schema load (run-level), retrieval, LLM, and evidence binding
(field-level). This module imports only stdlib.
"""

from __future__ import annotations


class SchemaExtractionError(Exception):
    """Base error for L2S2 Package B with a stable machine-readable code."""

    def __init__(self, message: str = "", *, error_code: str = "schema_extraction_error"):
        super().__init__(message or error_code)
        self.error_code = error_code


class SchemaLoadError(SchemaExtractionError):
    """Schema definition could not be loaded or validated (run-level).

    Distinct from ``not_found``: this is a system failure, never a signal
    that the paper simply has no answer for a field.
    """

    def __init__(self, message: str = "", *, schema_id: str | None = None):
        super().__init__(message, error_code="schema_load_failed")
        self.schema_id = schema_id


class RetrievalUnavailableError(SchemaExtractionError):
    """Retrieval service reported unavailable or raised at the boundary."""

    def __init__(self, message: str = "", *, error_code: str | None = None):
        super().__init__(message, error_code=error_code or "retrieval_unavailable")


class LLMUnavailableError(SchemaExtractionError):
    """LLM client is unavailable or not permitted to run (configuration error).

    Never converted to ``not_found``.
    """

    def __init__(self, message: str = "", *, provider: str | None = None):
        super().__init__(message, error_code="llm_unavailable")
        self.provider = provider


class LLMInvalidOutputError(SchemaExtractionError):
    """LLM returned structured output that fails the expected output schema."""

    def __init__(self, message: str = "", *, field_id: str | None = None):
        super().__init__(message, error_code="llm_invalid_output")
        self.field_id = field_id


class LLMRequestError(SchemaExtractionError):
    """Real LLM request failed at the transport level (FR-002/FR-003).

    Raised for HTTP errors, timeouts and exhausted retries (e.g. a persistent
    429 / 5xx). Never converted to ``not_found``: this is an explicit system
    failure. Error messages are redacted by the provider and never contain
    the API key.
    """

    def __init__(self, message: str = "", *, status_code: int | None = None):
        super().__init__(message, error_code="llm_request_failed")
        self.status_code = status_code


class LLMCapabilityError(LLMRequestError):
    """Provider explicitly rejected strict JSON Schema response formatting."""

    def __init__(self, message: str = "", *, status_code: int | None = None):
        super().__init__(message, status_code=status_code)
        self.error_code = "llm_structured_output_unsupported"


class UnknownEvidenceIdError(SchemaExtractionError):
    """LLM selected an evidence id that has no matching candidate."""

    def __init__(
        self,
        evidence_id: str,
        *,
        field_id: str | None = None,
        known_ids: list[str] | None = None,
    ):
        known = ", ".join(sorted(known_ids)) if known_ids else "none"
        message = f"unknown evidence id {evidence_id!r}; known candidate ids: {known}"
        if field_id:
            message = f"field {field_id!r}: {message}"
        super().__init__(message, error_code="unknown_evidence_id")
        self.field_id = field_id
        self.evidence_id = evidence_id


class EvidenceBindingError(SchemaExtractionError):
    """A selected candidate cannot be bound into an ``EvidenceRef``.

    Package B never fabricates provenance: when a candidate has no bindable
    source ref this error is raised instead of a fake ``EvidenceRef``.
    """

    def __init__(self, message: str = "", *, field_id: str | None = None):
        if not message and field_id:
            message = f"field {field_id!r}: evidence binding failed"
        super().__init__(message, error_code="evidence_binding_failed")
        self.field_id = field_id
