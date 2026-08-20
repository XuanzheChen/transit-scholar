"""L2S2 Package B deterministic tests: OpenAI-compatible real provider
(FR-002/FR-003 of task-2026-08-15-001).

Every test uses ``httpx.MockTransport`` or monkeypatches ``time`` — no real
network, no real key, no real base_url. The full l2s2 suite must stay green
with ``TRANSIT_SCHOLAR_BLOCK_NETWORK=1``, proving the default path never
networks.
"""

from __future__ import annotations

import json
from typing import Literal

import httpx
import pytest
from pydantic import BaseModel, ConfigDict, Field

from transit_scholar.layer2.schema_extraction import (
    LLMConfig,
    LLMInvalidOutputError,
    LLMRequestError,
    OpenAICompatibleLLMClient,
)

SENTINEL_KEY = "sk-e2e-test-redact-1234567890"


class DummyOutput(BaseModel):
    """Minimal output schema used to probe the real provider boundary."""

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


def _config(**overrides) -> LLMConfig:
    base = dict(
        provider="openai_compatible",
        model="test-model",
        api_key=SENTINEL_KEY,
        base_url="https://provider.invalid",
        allow_network=True,
        timeout_seconds=60,
        max_retries=2,
        rate_limit_rpm=1000,
    )
    base.update(overrides)
    return LLMConfig(**base)


def _json_response(content: dict, status_code: int = 200) -> httpx.Response:
    return httpx.Response(
        status_code=status_code,
        json={"choices": [{"message": {"content": json.dumps(content)}}]},
    )


def _client(handler, **config_overrides) -> OpenAICompatibleLLMClient:
    return OpenAICompatibleLLMClient(
        _config(**config_overrides), transport=httpx.MockTransport(handler)
    )


# ---------------------------------------------------------------------------
# success path (FR-002)
# ---------------------------------------------------------------------------


def test_success_path_returns_validated_model():
    def handler(request):
        return _json_response({"value": "holding", "status": "explicit"})

    client = _client(handler)
    out = client.generate_structured(
        [{"role": "user", "content": "u"}], DummyOutput, {"field_id": "f1"}
    )
    assert isinstance(out, DummyOutput)
    assert out.value == "holding"
    assert out.status == "explicit"
    assert client.is_fake is False
    assert client.provider_name == "openai_compatible"
    assert client.model_name == "test-model"


def test_success_path_strips_json_code_fence():
    def handler(request):
        return httpx.Response(
            status_code=200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": (
                                '```json\n{"value": "a", "status": "inferred"}\n```'
                            )
                        }
                    }
                ]
            },
        )

    client = _client(handler)
    out = client.generate_structured([{"role": "user", "content": "u"}], DummyOutput)
    assert out.value == "a"


def test_success_path_sends_expected_request_shape():
    captured = {}

    def handler(request):
        captured["url"] = str(request.url)
        captured["headers"] = request.headers
        captured["json"] = json.loads(request.content)
        return _json_response({"value": "x", "status": "explicit"})

    client = _client(handler)
    client.generate_structured(
        [{"role": "user", "content": "u"}], DummyOutput, {"field_id": "f1"}
    )
    assert captured["url"] == "https://provider.invalid/chat/completions"
    assert captured["headers"]["Authorization"] == f"Bearer {SENTINEL_KEY}"
    assert captured["json"]["model"] == "test-model"
    assert captured["json"]["messages"] == [{"role": "user", "content": "u"}]
    assert captured["json"]["response_format"] == {"type": "json_object"}


# ---------------------------------------------------------------------------
# failure paths (FR-002): explicit errors, never not_found
# ---------------------------------------------------------------------------


def test_non_json_body_raises_invalid_output():
    def handler(request):
        return httpx.Response(200, text="this is not json")

    client = _client(handler)
    with pytest.raises(LLMInvalidOutputError) as excinfo:
        client.generate_structured([{"role": "user", "content": "u"}], DummyOutput)
    assert excinfo.value.error_code == "llm_invalid_output"


def test_json_failing_schema_validation_raises_invalid_output():
    def handler(request):
        return _json_response({"value": "x", "status": "not_a_status"})

    client = _client(handler)
    with pytest.raises(LLMInvalidOutputError) as excinfo:
        client.generate_structured([{"role": "user", "content": "u"}], DummyOutput)
    assert excinfo.value.error_code == "llm_invalid_output"


def test_missing_choices_raises_invalid_output():
    def handler(request):
        return httpx.Response(200, json={"choices": []})

    client = _client(handler)
    with pytest.raises(LLMInvalidOutputError):
        client.generate_structured([{"role": "user", "content": "u"}], DummyOutput)


def test_http_401_fails_immediately_without_retry():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(401, text="unauthorized")

    client = _client(handler, max_retries=2)
    with pytest.raises(LLMRequestError) as excinfo:
        client.generate_structured([{"role": "user", "content": "u"}], DummyOutput)
    assert excinfo.value.error_code == "llm_request_failed"
    assert excinfo.value.status_code == 401
    assert calls["n"] == 1


def test_http_500_retried_then_explicit_error():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(500, text="server error")

    client = _client(handler, max_retries=2)
    with pytest.raises(LLMRequestError) as excinfo:
        client.generate_structured([{"role": "user", "content": "u"}], DummyOutput)
    assert excinfo.value.status_code == 500
    assert calls["n"] == 3  # 1 + max_retries


def test_timeout_raises_explicit_request_error():
    def handler(request):
        raise httpx.ReadTimeout("timed out", request=request)

    client = _client(handler)
    with pytest.raises(LLMRequestError) as excinfo:
        client.generate_structured([{"role": "user", "content": "u"}], DummyOutput)
    assert excinfo.value.error_code == "llm_request_failed"
    assert excinfo.value.status_code is None


# ---------------------------------------------------------------------------
# retry / backoff / rpm (FR-003)
# ---------------------------------------------------------------------------


def test_429_then_success_returns_model_and_counts_requests():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, text="rate limited")
        return _json_response({"value": "ok", "status": "explicit"})

    client = _client(handler, max_retries=2)
    out = client.generate_structured([{"role": "user", "content": "u"}], DummyOutput)
    assert out.value == "ok"
    assert calls["n"] == 2  # 1 initial + 1 retry


def test_429_exhausted_raises_explicit_error():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(429, text="rate limited")

    client = _client(handler, max_retries=2)
    with pytest.raises(LLMRequestError) as excinfo:
        client.generate_structured([{"role": "user", "content": "u"}], DummyOutput)
    assert excinfo.value.status_code == 429
    assert calls["n"] == 3  # exactly 1 + max_retries


def test_backoff_between_retries_is_positive(monkeypatch):
    sleeps = []

    def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr("transit_scholar.layer2.schema_extraction.llm.time.sleep",
                        fake_sleep)
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] <= 2:
            return httpx.Response(429, text="rate limited")
        return _json_response({"value": "ok", "status": "explicit"})

    client = _client(handler, max_retries=2, rate_limit_rpm=0)
    client.generate_structured([{"role": "user", "content": "u"}], DummyOutput)
    assert len(sleeps) == 2  # two backoff sleeps before the retries
    assert all(sleep_seconds > 0 for sleep_seconds in sleeps)
    assert calls["n"] == 3


def test_rpm_limit_enforces_minimum_interval(monkeypatch):
    """With rpm=60 the minimum inter-request interval is 1 second; the
    limiter must sleep the difference when calls come too close together."""
    sleeps = []
    now = {"t": 100.0}

    def fake_sleep(seconds):
        sleeps.append(seconds)
        now["t"] += seconds

    def fake_monotonic():
        return now["t"]

    monkeypatch.setattr("transit_scholar.layer2.schema_extraction.llm.time.sleep",
                        fake_sleep)
    monkeypatch.setattr(
        "transit_scholar.layer2.schema_extraction.llm.time.monotonic",
        fake_monotonic,
    )

    def handler(request):
        return _json_response({"value": "x", "status": "explicit"})

    client = _client(handler, rate_limit_rpm=60)
    client.generate_structured([{"role": "user", "content": "u"}], DummyOutput)
    # first call: nothing to wait for
    assert sleeps == []
    # second call 0.2s later: must sleep ~0.8s to reach the 1s interval
    now["t"] += 0.2
    client.generate_structured([{"role": "user", "content": "u"}], DummyOutput)
    assert len(sleeps) == 1
    assert sleeps[0] == pytest.approx(0.8, abs=1e-6)


# ---------------------------------------------------------------------------
# redaction (FR-001 hygiene on the real provider)
# ---------------------------------------------------------------------------


def test_sentinel_key_never_appears_in_error_strings():
    def handler(request):
        return httpx.Response(500, text="boom")

    client = _client(handler, max_retries=0)
    with pytest.raises(LLMRequestError) as excinfo:
        client.generate_structured([{"role": "user", "content": "u"}], DummyOutput)
    assert SENTINEL_KEY not in str(excinfo.value)


def test_sentinel_key_never_appears_in_invalid_output_errors():
    def handler(request):
        return _json_response({"value": "x", "status": "not_a_status"})

    client = _client(handler)
    with pytest.raises(LLMInvalidOutputError) as excinfo:
        client.generate_structured([{"role": "user", "content": "u"}], DummyOutput)
    assert SENTINEL_KEY not in str(excinfo.value)


def test_sentinel_key_never_appears_in_redacted_message_parts():
    """Even when the key value is embedded in a payload error path, the
    redaction helper strips it from the message."""
    client = _client(lambda request: httpx.Response(500, text="x"))
    assert SENTINEL_KEY not in client._redact(f"boom {SENTINEL_KEY} boom")
