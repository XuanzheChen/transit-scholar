"""Field semantic verification boundary for L2S2 Package C (FR-C-004 /
AC-C-04).

Decides whether the evidence quotes of one field support its extracted value.
The verifier is injectable and receives only the field definition, the field
result, and the evidence quote strings; it can never rewrite ``EvidenceRef``
provenance. The default verifier used by automated tests is
``FakeSemanticVerifier``: deterministic, offline, no LLM, no network.

Verdict vocabulary and its fixed issue mapping (AC-C-04 / G plan §4):

===============  ==============================  ========  ==========
verdict          issue type                     severity  action
===============  ==============================  ========  ==========
supported        (none)                          -         -
partially_supported  semantic_partially_supported  warning  None
unsupported      semantic_unsupported            warning   recheck
conflicting      semantic_conflicting            warning   recheck
unclear          semantic_unclear                warning   None
unavailable      verifier_unavailable            error     None
===============  ==============================  ========  ==========

``not_found`` / ``not_applicable`` fields are skipped entirely: an empty
evidence set for such a field is never judged ``unsupported``.

A verifier failure is a system failure: it produces an explicit
``verifier_unavailable`` error issue and is never masqueraded as
``not_found``.
"""

from __future__ import annotations

from typing import Any, Callable, Literal

from pydantic import BaseModel, Field

from .models import FieldDefinition, FieldResult, ValidationIssue

#: Fixed semantic verdict vocabulary (FR-C-004 + G plan decision point 2).
SemanticDecision = Literal[
    "supported",
    "partially_supported",
    "unsupported",
    "conflicting",
    "unclear",
]

#: Injectable verifier: (field, result, quotes) -> verdict.
SemanticVerifier = Callable[
    [FieldDefinition, FieldResult, list[str]], "SemanticVerdict"
]


class VerifierUnavailableError(Exception):
    """The semantic verifier could not run (system failure, never not_found)."""

    def __init__(self, message: str = ""):
        super().__init__(message or "semantic verifier unavailable")
        self.error_code = "verifier_unavailable"


class SemanticVerdict(BaseModel):
    """Structured semantic verification output (FR-C-004)."""

    decision: SemanticDecision
    confidence: float | None = Field(default=None, ge=0, le=1)
    notes: str = ""


class FakeSemanticVerifier:
    """Deterministic offline verifier for tests and offline defaults.

    Verdicts are looked up by field id: ``responses[field_id]`` wins, then
    ``default_response``. If neither is available, or the field id is listed
    in ``unavailable_keys``, ``VerifierUnavailableError`` is raised. Every
    call is recorded in ``calls`` for assertions.
    """

    def __init__(
        self,
        responses: dict[str, Any] | None = None,
        *,
        default_response: Any = None,
        unavailable_keys: list[str] | None = None,
    ):
        self.responses = dict(responses or {})
        self.default_response = default_response
        self.unavailable_keys = list(unavailable_keys or [])
        self.calls: list[tuple[str, list[str]]] = []

    def __call__(
        self,
        field: FieldDefinition,
        result: FieldResult,
        quotes: list[str],
    ) -> SemanticVerdict:
        self.calls.append((field.id, list(quotes)))
        if field.id in self.unavailable_keys:
            raise VerifierUnavailableError(
                f"fake semantic verifier has no response for field {field.id!r}"
            )
        preset = self.responses.get(field.id, self.default_response)
        if preset is None:
            raise VerifierUnavailableError(
                f"fake semantic verifier has no response for field {field.id!r}"
            )
        return SemanticVerdict.model_validate(preset)


def verify_field_semantics(
    field: FieldDefinition,
    result: FieldResult,
    verifier: SemanticVerifier | None,
) -> list[ValidationIssue]:
    """Verify that the evidence of one field supports its value.

    ``not_found`` / ``not_applicable`` fields are skipped and the verifier is
    not called. Verifier failures become an explicit ``verifier_unavailable``
    error. The ``result`` object is never mutated.
    """
    if result.status in ("not_found", "not_applicable"):
        return []

    quotes = [ref.quote for ref in result.evidence if ref.quote]

    if verifier is None:
        return [_unavailable_issue(field, "no semantic verifier provided")]

    try:
        verdict = verifier(field, result, quotes)
    except Exception as exc:  # noqa: BLE001 - explicit system failure
        return [
            _unavailable_issue(
                field,
                f"semantic verifier raised {type(exc).__name__}: {exc}",
            )
        ]

    try:
        verdict = SemanticVerdict.model_validate(verdict)
    except Exception as exc:  # noqa: BLE001 - explicit system failure
        return [
            _unavailable_issue(
                field,
                f"semantic verifier returned an invalid verdict "
                f"({type(exc).__name__})",
            )
        ]

    if verdict.decision == "supported":
        return []
    if verdict.decision == "partially_supported":
        return [
            ValidationIssue(
                type="semantic_partially_supported",
                severity="warning",
                message=(
                    f"field {field.id!r} evidence only partially supports the "
                    f"extracted value"
                ),
                fields=[field.id],
            )
        ]
    if verdict.decision == "unsupported":
        return [
            ValidationIssue(
                type="semantic_unsupported",
                severity="warning",
                message=(
                    f"field {field.id!r} evidence does not support the "
                    f"extracted value"
                ),
                fields=[field.id],
                action="recheck",
            )
        ]
    if verdict.decision == "conflicting":
        return [
            ValidationIssue(
                type="semantic_conflicting",
                severity="warning",
                message=(
                    f"field {field.id!r} evidence conflicts with the "
                    f"extracted value"
                ),
                fields=[field.id],
                action="recheck",
            )
        ]
    return [
        ValidationIssue(
            type="semantic_unclear",
            severity="warning",
            message=(
                f"field {field.id!r} evidence is insufficient to confirm the "
                f"extracted value"
            ),
            fields=[field.id],
        )
    ]


def _unavailable_issue(
    field: FieldDefinition,
    message: str,
) -> ValidationIssue:
    return ValidationIssue(
        type="verifier_unavailable",
        severity="error",
        message=message,
        fields=[field.id],
    )
