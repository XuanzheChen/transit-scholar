"""L2S2 Package B deterministic tests: unified LLM client boundary.

Covers requirements.md FR-B-001/002 and acceptance criteria AC-L2S2B-01..04:
client abstraction, provider-agnostic boundary, deterministic fake provider,
config boundary (env vars, offline default, explicit unavailable for
non-fake without network permission), and the LLM output field restriction
(no provenance fields). Also covers the FR-001 task-2026-08-15-001 config
extension: timeout / retries / rpm parsing, real-provider resolve validation
and API-key hygiene.
"""

from __future__ import annotations

from typing import Literal

import pytest
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from transit_scholar.layer2.schema_extraction import (
    FakeLLMProvider,
    LLMConfig,
    RealLLMClientStub,
    LLMInvalidOutputError,
    LLMUnavailableError,
    OpenAICompatibleLLMClient,
    resolve_llm_client,
)
from transit_scholar.layer2.schema_extraction.engine import FieldExtractionLLMOutput

LLM_ENV_VARS = (
    "TRANSIT_SCHOLAR_LLM_PROVIDER",
    "TRANSIT_SCHOLAR_LLM_MODEL",
    "TRANSIT_SCHOLAR_LLM_API_KEY",
    "TRANSIT_SCHOLAR_LLM_BASE_URL",
    "TRANSIT_SCHOLAR_LLM_ALLOW_NETWORK",
    "TRANSIT_SCHOLAR_LLM_TIMEOUT_SECONDS",
    "TRANSIT_SCHOLAR_LLM_MAX_RETRIES",
    "TRANSIT_SCHOLAR_LLM_RATE_LIMIT_RPM",
)


class DummyOutput(BaseModel):
    """Minimal standalone output schema used to probe the client boundary."""

    model_config = ConfigDict(extra="forbid")

    value: str | None = None
    status: Literal[
        "explicit",
        "inferred",
        "unclear",
        "not_found",
        "not_applicable",
        "conflicting",
    ]
    confidence: float | None = Field(default=None, ge=0, le=1)


# ---------------------------------------------------------------------------
# AC-L2S2B-01 unified client abstraction / provider-agnostic boundary
# ---------------------------------------------------------------------------


def test_client_abstraction_and_call_signature():
    from transit_scholar.layer2.schema_extraction import StructuredLLMClient

    assert callable(StructuredLLMClient)  # runtime-checkable protocol
    assert hasattr(StructuredLLMClient, "generate_structured")


def test_fake_provider_is_deterministic_and_returns_validated_model():
    provider = FakeLLMProvider(
        responses={"f1": {"value": "holding", "status": "explicit"}},
    )
    out = provider.generate_structured(
        [{"role": "user", "content": "extract f1"}],
        DummyOutput,
        {"field_id": "f1", "prompt_key": "f1"},
    )
    assert isinstance(out, DummyOutput)
    assert out.value == "holding"
    assert out.status == "explicit"


def test_llm_client_module_has_no_business_logic_imports():
    """AC-L2S2B-01: client boundary contains no field extraction, retrieval,
    or evidence binding logic. The ``llm`` module's own namespace binds no
    sibling business modules (the package ``__init__`` is not the client)."""
    import transit_scholar.layer2.schema_extraction.llm as llm_mod

    for name in ("engine", "evidence", "retrieval", "query", "trace", "loader", "models"):
        assert name not in llm_mod.__dict__, name


# ---------------------------------------------------------------------------
# AC-L2S2B-02 deterministic fake provider
# ---------------------------------------------------------------------------


def test_fake_provider_requires_no_config_or_network():
    provider = FakeLLMProvider(default_response={"value": "x", "status": "explicit"})
    out = provider.generate_structured([{"role": "user", "content": "u"}], DummyOutput)
    assert out.status == "explicit"


def test_fake_provider_preset_keyed_by_field_id():
    provider = FakeLLMProvider(
        responses={"f1": {"value": "a", "status": "explicit"}},
    )
    out = provider.generate_structured([{"role": "user", "content": "u"}], DummyOutput, {"field_id": "f1"})
    assert out.value == "a"


def test_fake_provider_preset_keyed_by_prompt_key():
    provider = FakeLLMProvider(
        responses={"key_b": {"value": "b", "status": "inferred"}},
    )
    out = provider.generate_structured([{"role": "user", "content": "u"}], DummyOutput, {"prompt_key": "key_b"})
    assert out.value == "b"


def test_fake_provider_records_calls_in_order():
    provider = FakeLLMProvider(
        responses={
            "f1": {"value": "a", "status": "explicit"},
            "f2": {"value": "b", "status": "inferred"},
        }
    )
    provider.generate_structured([{"role": "user", "content": "u1"}], DummyOutput, {"field_id": "f1", "prompt_key": "f1"})
    provider.generate_structured([{"role": "user", "content": "u2"}], DummyOutput, {"field_id": "f2", "prompt_key": "f2"})
    assert [call.prompt_key for call in provider.calls] == ["f1", "f2"]
    assert [call.outcome for call in provider.calls] == ["ok", "ok"]
    assert provider.calls[0].messages == [{"role": "user", "content": "u1"}]
    assert provider.calls[0].metadata == {"field_id": "f1", "prompt_key": "f1"}


def test_fake_provider_default_response_fallback():
    provider = FakeLLMProvider(
        responses={"f1": {"value": "a", "status": "explicit"}},
        default_response={"value": "fallback", "status": "unclear"},
    )
    out = provider.generate_structured([{"role": "user", "content": "u"}], DummyOutput, {"field_id": "f9"})
    assert out.value == "fallback"


def test_fake_provider_can_return_invalid_output():
    provider = FakeLLMProvider(
        responses={"f1": {"value": "x", "status": "not_a_status"}},
    )
    with pytest.raises(LLMInvalidOutputError):
        provider.generate_structured([{"role": "user", "content": "u"}], DummyOutput, {"field_id": "f1"})
    assert provider.calls[0].outcome == "invalid_output"
    assert provider.calls[0].prompt_key == "f1"


def test_fake_provider_no_preset_and_no_default_raises_invalid_output():
    provider = FakeLLMProvider()
    with pytest.raises(LLMInvalidOutputError) as excinfo:
        provider.generate_structured([{"role": "user", "content": "u"}], DummyOutput, {"field_id": "f1"})
    assert "f1" in str(excinfo.value)
    assert provider.calls[0].outcome == "invalid_output"


# ---------------------------------------------------------------------------
# AC-L2S2B-04 LLM output field restriction (provenance keys rejected)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "provenance_key",
    ["block_id", "char_start", "char_end", "pages", "section_path", "quote"],
)
def test_fake_provider_rejects_provenance_fields(provenance_key):
    preset = {"value": "x", "status": "explicit", provenance_key: "forbidden"}
    provider = FakeLLMProvider(responses={"f1": preset})
    with pytest.raises(LLMInvalidOutputError):
        provider.generate_structured(
            [{"role": "user", "content": "u"}],
            FieldExtractionLLMOutput,
            {"field_id": "f1"},
        )


def test_field_extraction_output_rejects_unknown_keys():
    from transit_scholar.layer2.schema_extraction.engine import FieldExtractionLLMOutput

    with pytest.raises(ValidationError):
        FieldExtractionLLMOutput.model_validate(
            {"value": "x", "status": "explicit", "pages": [3]}
        )
    with pytest.raises(ValidationError):
        FieldExtractionLLMOutput.model_validate(
            {"value": "x", "status": "explicit", "quote": "not allowed"}
        )


def test_field_extraction_output_accepts_only_allowed_fields():
    from transit_scholar.layer2.schema_extraction.engine import FieldExtractionLLMOutput

    out = FieldExtractionLLMOutput.model_validate(
        {
            "value": "holding",
            "status": "explicit",
            "evidence_ids": ["E1"],
            "confidence": 0.9,
            "notes": "ok",
        }
    )
    assert out.evidence_ids == ["E1"]
    assert out.confidence == 0.9


# ---------------------------------------------------------------------------
# AC-L2S2B-03 provider config boundary
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_llm_env(monkeypatch):
    for name in LLM_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def test_llm_config_defaults_offline_fake():
    config = LLMConfig()
    assert config.provider is None
    assert config.allow_network is False
    assert isinstance(resolve_llm_client(config), FakeLLMProvider)


def test_llm_config_from_env_reads_five_vars(monkeypatch):
    monkeypatch.setenv("TRANSIT_SCHOLAR_LLM_PROVIDER", "openai")
    monkeypatch.setenv("TRANSIT_SCHOLAR_LLM_MODEL", "gpt-test")
    monkeypatch.setenv("TRANSIT_SCHOLAR_LLM_API_KEY", "sk-test")
    monkeypatch.setenv("TRANSIT_SCHOLAR_LLM_BASE_URL", "https://example.invalid")
    monkeypatch.setenv("TRANSIT_SCHOLAR_LLM_ALLOW_NETWORK", "1")
    config = LLMConfig.from_env()
    assert config.provider == "openai"
    assert config.model == "gpt-test"
    assert config.api_key == "sk-test"
    assert config.base_url == "https://example.invalid"
    assert config.allow_network is True


def test_llm_config_from_env_blank_values_none(monkeypatch):
    monkeypatch.setenv("TRANSIT_SCHOLAR_LLM_PROVIDER", "  ")
    monkeypatch.setenv("TRANSIT_SCHOLAR_LLM_MODEL", "")
    config = LLMConfig.from_env()
    assert config.provider is None
    assert config.model is None


def test_resolve_default_provider_is_fake(monkeypatch):
    monkeypatch.delenv("TRANSIT_SCHOLAR_LLM_PROVIDER", raising=False)
    client = resolve_llm_client()
    assert isinstance(client, FakeLLMProvider)
    assert client.is_fake is True


def test_resolve_non_fake_without_network_raises_unavailable():
    config = LLMConfig(provider="openai", model="gpt-x", allow_network=False)
    with pytest.raises(LLMUnavailableError) as excinfo:
        resolve_llm_client(config)
    assert "not_found" not in str(excinfo.value)
    assert excinfo.value.error_code == "llm_unavailable"


def test_real_stub_call_raises_unavailable_never_not_found():
    config = LLMConfig(provider="openai", model="gpt-x", allow_network=True)
    client = resolve_llm_client(config)
    assert isinstance(client, RealLLMClientStub)
    assert client.is_fake is False
    with pytest.raises(LLMUnavailableError) as excinfo:
        client.generate_structured([{"role": "user", "content": "u"}], DummyOutput)
    assert "not_found" not in str(excinfo.value)
    assert excinfo.value.provider == "openai"


def test_resolve_non_fake_without_network_from_env_raises(monkeypatch):
    monkeypatch.setenv("TRANSIT_SCHOLAR_LLM_PROVIDER", "anthropic")
    with pytest.raises(LLMUnavailableError):
        resolve_llm_client()


# ---------------------------------------------------------------------------
# FR-001 (task-2026-08-15-001): LLMConfig extension + real-provider resolve
# ---------------------------------------------------------------------------


def test_llm_config_new_knob_defaults():
    config = LLMConfig()
    assert config.timeout_seconds == 60
    assert config.max_retries == 2
    assert config.rate_limit_rpm == 20


def test_llm_config_from_env_reads_new_knobs(monkeypatch):
    monkeypatch.setenv("TRANSIT_SCHOLAR_LLM_TIMEOUT_SECONDS", "30")
    monkeypatch.setenv("TRANSIT_SCHOLAR_LLM_MAX_RETRIES", "5")
    monkeypatch.setenv("TRANSIT_SCHOLAR_LLM_RATE_LIMIT_RPM", "100")
    config = LLMConfig.from_env()
    assert config.timeout_seconds == 30
    assert config.max_retries == 5
    assert config.rate_limit_rpm == 100


def test_llm_config_from_env_blank_knobs_use_defaults(monkeypatch):
    monkeypatch.setenv("TRANSIT_SCHOLAR_LLM_TIMEOUT_SECONDS", "  ")
    monkeypatch.setenv("TRANSIT_SCHOLAR_LLM_MAX_RETRIES", "")
    monkeypatch.setenv("TRANSIT_SCHOLAR_LLM_RATE_LIMIT_RPM", "   ")
    config = LLMConfig.from_env()
    assert config.timeout_seconds == 60
    assert config.max_retries == 2
    assert config.rate_limit_rpm == 20


@pytest.mark.parametrize(
    "name",
    ["TRANSIT_SCHOLAR_LLM_TIMEOUT_SECONDS",
     "TRANSIT_SCHOLAR_LLM_MAX_RETRIES",
     "TRANSIT_SCHOLAR_LLM_RATE_LIMIT_RPM"],
)
def test_llm_config_from_env_non_integer_knob_raises_value_error(name, monkeypatch):
    monkeypatch.setenv(name, "not-an-int")
    with pytest.raises(ValueError):
        LLMConfig.from_env()


@pytest.mark.parametrize("missing_field", ["api_key", "base_url", "model"])
def test_resolve_real_provider_missing_field_raises_unavailable(missing_field):
    config = LLMConfig(
        provider="openai_compatible",
        model="gpt-test",
        api_key="sk-e2e-test-0000",
        base_url="https://example.invalid",
        allow_network=True,
    )
    setattr(config, missing_field, None)
    with pytest.raises(LLMUnavailableError) as excinfo:
        resolve_llm_client(config)
    assert excinfo.value.error_code == "llm_unavailable"
    assert missing_field in str(excinfo.value)
    assert "not_found" not in str(excinfo.value)


def test_resolve_real_provider_complete_returns_real_client():
    config = LLMConfig(
        provider="openai_compatible",
        model="gpt-test",
        api_key="sk-e2e-test-0000",
        base_url="https://example.invalid",
        allow_network=True,
        timeout_seconds=30,
        max_retries=1,
        rate_limit_rpm=10,
    )
    client = resolve_llm_client(config)
    assert isinstance(client, OpenAICompatibleLLMClient)
    assert client.is_fake is False
    assert client.provider_name == "openai_compatible"
    assert client.model_name == "gpt-test"


def test_real_provider_resolve_never_attempts_network_on_missing_fields():
    """Missing-field resolve errors happen before any client is built, so no
    request object / URL is ever constructed."""
    config = LLMConfig(
        provider="openai_compatible",
        model=None,
        api_key=None,
        base_url=None,
        allow_network=True,
    )
    with pytest.raises(LLMUnavailableError) as excinfo:
        resolve_llm_client(config)
    assert "api_key" in str(excinfo.value)
    assert "not_found" not in str(excinfo.value)


# ---------------------------------------------------------------------------
# FR-001 (task-2026-08-15-001): API-key hygiene
# ---------------------------------------------------------------------------

SENTINEL_KEY = "sk-e2e-test-redact-1234567890"


def test_api_key_never_in_unavailable_error_strings():
    config = LLMConfig(
        provider="openai_compatible",
        model="gpt-test",
        api_key=SENTINEL_KEY,
        base_url=None,
        allow_network=True,
    )
    with pytest.raises(LLMUnavailableError) as excinfo:
        resolve_llm_client(config)
    assert SENTINEL_KEY not in str(excinfo.value)


def test_api_key_never_in_allow_network_error_strings():
    config = LLMConfig(
        provider="openai_compatible",
        model="gpt-test",
        api_key=SENTINEL_KEY,
        base_url="https://example.invalid",
        allow_network=False,
    )
    with pytest.raises(LLMUnavailableError) as excinfo:
        resolve_llm_client(config)
    assert SENTINEL_KEY not in str(excinfo.value)
    assert "api_key" not in str(excinfo.value).lower()


def test_api_key_never_in_fake_call_records():
    """The sentinel key must not leak into fake call records even when the
    key was configured (fake provider never receives it, but guard anyway)."""
    provider = FakeLLMProvider(responses={"f1": {"value": "x", "status": "explicit"}})
    provider.generate_structured(
        [{"role": "user", "content": "u"}], DummyOutput, {"field_id": "f1"}
    )
    record = provider.calls[0]
    assert SENTINEL_KEY not in record.model_dump_json()
    assert SENTINEL_KEY not in record.messages
    assert SENTINEL_KEY not in record.metadata
    assert SENTINEL_KEY not in (record.output or {})
