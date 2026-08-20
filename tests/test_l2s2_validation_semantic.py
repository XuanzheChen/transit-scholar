"""L2S2 Package C deterministic tests: semantic verifier boundary
(FR-C-004 / AC-C-04).

The fake verifier is deterministic and offline. Verdict-to-issue mapping is
fixed by the G plan: unsupported -> warning with action="recheck";
conflicting -> warning with action="recheck"; partially_supported and
unclear -> warning without recheck; supported -> no issue; verifier failure
-> ``verifier_unavailable`` error, never masqueraded as ``not_found``.
"""

from __future__ import annotations

import pytest

from transit_scholar.layer2.schema_extraction import (
    EvidenceRef,
    FieldDefinition,
    FieldResult,
    FakeSemanticVerifier,
    SemanticVerdict,
    VerifierUnavailableError,
    verify_field_semantics,
)


def _field(field_id: str = "f") -> FieldDefinition:
    return FieldDefinition(
        id=field_id,
        label="Label",
        question="Question?",
        type="string",
    )


def _result(
    quotes: list[str],
    *,
    status: str = "explicit",
) -> FieldResult:
    refs = [
        EvidenceRef(
            block_id=f"blk_{i}",
            char_start=0,
            char_end=max(1, len(q)),
            quote=q,
        )
        for i, q in enumerate(quotes)
    ]
    return FieldResult(value="v", status=status, evidence=refs)


# ---------------------------------------------------------------------------
# verdict -> issue mapping
# ---------------------------------------------------------------------------


def test_supported_produces_no_issue():
    verifier = FakeSemanticVerifier(
        responses={"f": {"decision": "supported", "confidence": 0.9, "notes": "ok"}}
    )
    issues = verify_field_semantics(
        _field(), _result(["the value is v"]), verifier
    )
    assert issues == []


def test_partially_supported_is_warning_without_recheck():
    verifier = FakeSemanticVerifier(
        responses={"f": {"decision": "partially_supported", "confidence": 0.6}}
    )
    issues = verify_field_semantics(
        _field(), _result(["part of the value"]), verifier
    )
    assert [i.type for i in issues] == ["semantic_partially_supported"]
    assert issues[0].severity == "warning"
    assert issues[0].action is None


def test_unsupported_is_warning_with_recheck():
    verifier = FakeSemanticVerifier(
        responses={"f": {"decision": "unsupported", "confidence": 0.4}}
    )
    issues = verify_field_semantics(_field(), _result(["unrelated"]), verifier)
    assert [i.type for i in issues] == ["semantic_unsupported"]
    assert issues[0].severity == "warning"
    assert issues[0].action == "recheck"
    assert issues[0].fields == ["f"]


def test_conflicting_is_warning_with_recheck():
    verifier = FakeSemanticVerifier(
        responses={"f": {"decision": "conflicting", "confidence": 0.5}}
    )
    issues = verify_field_semantics(_field(), _result(["other value"]), verifier)
    assert [i.type for i in issues] == ["semantic_conflicting"]
    assert issues[0].severity == "warning"
    assert issues[0].action == "recheck"


def test_unclear_is_warning_without_recheck():
    verifier = FakeSemanticVerifier(
        responses={"f": {"decision": "unclear", "confidence": None}}
    )
    result = _result(["vague"])
    before = result.model_dump()
    issues = verify_field_semantics(_field(), result, verifier)
    assert [i.type for i in issues] == ["semantic_unclear"]
    assert issues[0].severity == "warning"
    assert issues[0].action is None
    assert result.model_dump() == before


def test_default_response_is_used_when_no_preset():
    verifier = FakeSemanticVerifier(
        default_response={"decision": "supported", "confidence": None}
    )
    assert verify_field_semantics(_field(), _result(["x"]), verifier) == []


# ---------------------------------------------------------------------------
# verifier unavailable is explicit, never not_found
# ---------------------------------------------------------------------------


def test_unavailable_key_is_explicit_error():
    verifier = FakeSemanticVerifier(
        responses={}, unavailable_keys=["f"]
    )
    issues = verify_field_semantics(_field(), _result(["x"]), verifier)
    assert [i.type for i in issues] == ["verifier_unavailable"]
    assert issues[0].severity == "error"
    assert "not_found" not in issues[0].type


def test_missing_preset_and_default_raises_unavailable():
    verifier = FakeSemanticVerifier(responses={})
    with pytest.raises(VerifierUnavailableError):
        verifier(_field(), _result(["x"]), ["x"])
    issues = verify_field_semantics(_field(), _result(["x"]), verifier)
    assert [i.type for i in issues] == ["verifier_unavailable"]
    assert issues[0].severity == "error"


def test_verifier_exception_is_explicit_error():
    def exploding_verifier(field, result, quotes):
        raise RuntimeError("verifier down")

    issues = verify_field_semantics(
        _field(), _result(["x"]), exploding_verifier
    )
    assert [i.type for i in issues] == ["verifier_unavailable"]
    assert issues[0].severity == "error"
    assert "RuntimeError" in issues[0].message


def test_verifier_invalid_output_is_explicit_error():
    def garbage_verifier(field, result, quotes):
        return "not a verdict"

    issues = verify_field_semantics(
        _field(), _result(["x"]), garbage_verifier
    )
    assert [i.type for i in issues] == ["verifier_unavailable"]
    assert issues[0].severity == "error"


def test_verifier_none_is_explicit_error():
    issues = verify_field_semantics(_field(), _result(["x"]), None)
    assert [i.type for i in issues] == ["verifier_unavailable"]
    assert issues[0].severity == "error"


def test_verifier_accepts_dict_verdict():
    def dict_verifier(field, result, quotes):
        return {"decision": "unsupported", "confidence": 0.2, "notes": "n"}

    issues = verify_field_semantics(_field(), _result(["x"]), dict_verifier)
    assert [i.type for i in issues] == ["semantic_unsupported"]


# ---------------------------------------------------------------------------
# provenance protection and quote plumbing
# ---------------------------------------------------------------------------


def test_provenance_is_never_rewritten():
    verifier = FakeSemanticVerifier(
        responses={"f": {"decision": "unclear", "confidence": None}}
    )
    result = _result(["quote one", "quote two"])
    before = result.model_dump()
    verify_field_semantics(_field(), result, verifier)
    assert result.model_dump() == before


def test_only_nonempty_quotes_are_passed():
    verifier = FakeSemanticVerifier(
        responses={"f": {"decision": "supported", "confidence": None}}
    )
    result = _result(["a", "", "b"])
    verify_field_semantics(_field(), result, verifier)
    assert verifier.calls == [("f", ["a", "b"])]


def test_verdict_model_rejects_bad_confidence():
    with pytest.raises(Exception):
        SemanticVerdict.model_validate({"decision": "supported", "confidence": 1.5})


# ---------------------------------------------------------------------------
# not_found / not_applicable fields skip semantic verification
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", ["not_found", "not_applicable"])
def test_non_assertive_status_skips_verification(status):
    verifier = FakeSemanticVerifier(
        responses={"f": {"decision": "unsupported", "confidence": 0.0}}
    )
    result = _result([], status=status)
    issues = verify_field_semantics(_field(), result, verifier)
    assert issues == []
    assert verifier.calls == []


# ---------------------------------------------------------------------------
# determinism
# ---------------------------------------------------------------------------


def test_semantic_verification_is_deterministic():
    verifier = FakeSemanticVerifier(
        responses={"f": {"decision": "unsupported", "confidence": 0.3}}
    )
    result = _result(["x"])
    first = [
        i.model_dump() for i in verify_field_semantics(_field(), result, verifier)
    ]
    second = [
        i.model_dump() for i in verify_field_semantics(_field(), result, verifier)
    ]
    assert first == second
