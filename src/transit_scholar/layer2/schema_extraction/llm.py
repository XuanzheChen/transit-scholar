"""Unified LLM client boundary for L2S2 schema extraction (FR-B-001/002/003).

The client layer is provider-agnostic: it only turns ``messages`` + an
expected output structure (a Pydantic model) + metadata into a validated
structured object. It contains no field extraction, retrieval, or evidence
binding logic.

Configuration boundary (FR-B-001):

- ``TRANSIT_SCHOLAR_LLM_PROVIDER``
- ``TRANSIT_SCHOLAR_LLM_MODEL``
- ``TRANSIT_SCHOLAR_LLM_API_KEY``
- ``TRANSIT_SCHOLAR_LLM_BASE_URL``
- ``TRANSIT_SCHOLAR_LLM_ALLOW_NETWORK``
- ``TRANSIT_SCHOLAR_LLM_TIMEOUT_SECONDS``
- ``TRANSIT_SCHOLAR_LLM_MAX_RETRIES``
- ``TRANSIT_SCHOLAR_LLM_RATE_LIMIT_RPM``

The default provider is the deterministic offline ``FakeLLMProvider``.
``RealLLMClientStub`` is only a configuration boundary: Package B never makes
real network calls, and calling it always raises ``LLMUnavailableError``
(never ``not_found``). ``OpenAICompatibleLLMClient`` is the real,
network-gated OpenAI-compatible provider: it only becomes reachable when the
user explicitly sets ``TRANSIT_SCHOLAR_LLM_ALLOW_NETWORK=1`` together with a
key / base URL / model. ``LLMConfig`` reads ``os.environ`` directly and never
imports ``transit_scholar.config``, keeping the deterministic import path
free of the L2S1 stack.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ValidationError

from .errors import LLMInvalidOutputError, LLMRequestError, LLMUnavailableError

_ENV_PROVIDER = "TRANSIT_SCHOLAR_LLM_PROVIDER"
_ENV_MODEL = "TRANSIT_SCHOLAR_LLM_MODEL"
_ENV_API_KEY = "TRANSIT_SCHOLAR_LLM_API_KEY"
_ENV_BASE_URL = "TRANSIT_SCHOLAR_LLM_BASE_URL"
_ENV_ALLOW_NETWORK = "TRANSIT_SCHOLAR_LLM_ALLOW_NETWORK"
_ENV_TIMEOUT = "TRANSIT_SCHOLAR_LLM_TIMEOUT_SECONDS"
_ENV_MAX_RETRIES = "TRANSIT_SCHOLAR_LLM_MAX_RETRIES"
_ENV_RATE_LIMIT_RPM = "TRANSIT_SCHOLAR_LLM_RATE_LIMIT_RPM"

#: Provider name mapped to the real OpenAI-compatible client.
REAL_PROVIDER = "openai_compatible"

_TRUTHY = frozenset({"1", "true", "yes", "on"})


def _env(name: str) -> str | None:
    value = os.environ.get(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _env_bool(name: str) -> bool:
    value = _env(name)
    return value is not None and value.strip().lower() in _TRUTHY


def _env_int(name: str, default: int) -> int:
    value = _env(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        raise ValueError(
            f"environment variable {name} must be an integer, got {value!r}"
        )


@dataclass
class LLMConfig:
    """Provider configuration boundary (FR-B-001). Defaults are offline/fake.

    ``timeout_seconds`` / ``max_retries`` / ``rate_limit_rpm`` are request
    control knobs consumed by the real OpenAI-compatible provider only.
    """

    provider: str | None = None
    model: str | None = None
    api_key: str | None = None
    base_url: str | None = None
    allow_network: bool = False
    timeout_seconds: int = 60
    max_retries: int = 2
    rate_limit_rpm: int = 20

    @classmethod
    def from_env(cls) -> "LLMConfig":
        """Build config from the ``TRANSIT_SCHOLAR_LLM_*`` environment
        variables. Reads ``os.environ`` only; never touches ``.env`` or the
        project config module. Blank/absent knobs fall back to defaults;
        non-integer values raise ``ValueError`` deterministically."""
        return cls(
            provider=_env(_ENV_PROVIDER),
            model=_env(_ENV_MODEL),
            api_key=_env(_ENV_API_KEY),
            base_url=_env(_ENV_BASE_URL),
            allow_network=_env_bool(_ENV_ALLOW_NETWORK),
            timeout_seconds=_env_int(_ENV_TIMEOUT, 60),
            max_retries=_env_int(_ENV_MAX_RETRIES, 2),
            rate_limit_rpm=_env_int(_ENV_RATE_LIMIT_RPM, 20),
        )


@runtime_checkable
class StructuredLLMClient(Protocol):
    """Unified structured-generation boundary.

    ``generate_structured`` returns an instance of ``output_schema`` (a
    Pydantic model). Invalid output raises ``LLMInvalidOutputError``;
    unavailable/forbidden clients raise ``LLMUnavailableError``.
    """

    is_fake: bool
    provider_name: str
    model_name: str

    def generate_structured(
        self,
        messages: list[dict[str, Any]],
        output_schema: type[BaseModel],
        metadata: dict[str, Any] | None = None,
    ) -> BaseModel:
        ...


def _json_safe(value: Any) -> Any:
    try:
        import json

        json.dumps(value)
    except (TypeError, ValueError):
        return repr(value)
    return value


class FakeLLMProvider:
    """Deterministic offline fake provider (FR-B-001, AC-L2S2B-02).

    Preset results are keyed by field id or prompt key (from ``metadata``);
    an optional ``default_response`` is used when no preset matches. Every
    call is recorded in ``calls`` in invocation order so tests can assert
    requested keys and ordering. Output is validated against the requested
    ``output_schema``; invalid presets raise ``LLMInvalidOutputError`` and are
    still recorded (``outcome="invalid_output"``).
    """

    is_fake = True
    provider_name = "fake"
    model_name = "fake-v0"

    def __init__(
        self,
        responses: dict[str, Any] | None = None,
        default_response: dict[str, Any] | list[dict[str, Any]] | None = None,
    ):
        self.responses: dict[str, Any] = dict(responses or {})
        self.default_response: dict[str, Any] | list[dict[str, Any]] | None = (
            default_response
        )
        self.calls: list[FakeCallRecord] = []

    def _consume(self, value: Any) -> Any:
        """Pop one response from a per-key sequence (list) or return as-is.

        A list is treated as an ordered sequence: each call consumes the front
        element so retry tests can script ``[first_attempt, retry]`` responses.
        When the sequence is exhausted the next lookup falls back to the
        ordinary no-preset path (``LLMInvalidOutputError``).
        """
        if isinstance(value, list) and value:
            return value[0]
        if isinstance(value, list):
            return None
        return value

    def _store_back(self, key: str | None, value: Any) -> None:
        """Write back the remaining sequence (stateful fake behavior)."""
        if isinstance(value, list) and value:
            if key and key in self.responses:
                self.responses[key] = value[1:]
            else:
                self.default_response = value[1:]
        elif isinstance(value, list) and not value:
            if key and key in self.responses:
                del self.responses[key]
            else:
                self.default_response = None

    def _lookup(self, key: str | None) -> dict[str, Any] | None:
        if key and key in self.responses:
            value = self.responses[key]
            item = self._consume(value)
            self._store_back(key, value)
            return item
        if self.default_response is not None:
            value = self.default_response
            item = self._consume(value)
            self._store_back(None, value)
            return item
        return None

    def generate_structured(
        self,
        messages: list[dict[str, Any]],
        output_schema: type[BaseModel],
        metadata: dict[str, Any] | None = None,
    ) -> BaseModel:
        metadata = dict(metadata or {})
        key = metadata.get("prompt_key") or metadata.get("field_id")
        raw = self._lookup(key)
        record = FakeCallRecord(
            prompt_key=key,
            messages=list(messages),
            metadata=metadata,
            output=_json_safe(raw),
        )
        if raw is None:
            record.outcome = "invalid_output"
            self.calls.append(record)
            raise LLMInvalidOutputError(
                f"fake provider has no preset response for key {key!r} "
                "and no default_response",
                field_id=metadata.get("field_id"),
            )
        try:
            parsed = output_schema.model_validate(raw)
        except (ValidationError, TypeError, ValueError) as exc:
            record.outcome = "invalid_output"
            self.calls.append(record)
            raise LLMInvalidOutputError(
                f"invalid structured output for key {key!r}: {exc}",
                field_id=metadata.get("field_id"),
            ) from exc
        record.outcome = "ok"
        self.calls.append(record)
        return parsed


class RealLLMClientStub:
    """Real-provider configuration boundary only (FR-B-001, AC-L2S2B-03).

    Package B never performs real LLM calls. Calling this client always
    raises ``LLMUnavailableError`` — an explicit unavailable/configuration
    error, never ``not_found``.
    """

    is_fake = False

    def __init__(self, config: LLMConfig | None = None):
        self.config = config or LLMConfig()

    @property
    def provider_name(self) -> str:
        return self.config.provider or "real"

    @property
    def model_name(self) -> str:
        return self.config.model or "unknown"

    def generate_structured(
        self,
        messages: list[dict[str, Any]],
        output_schema: type[BaseModel],
        metadata: dict[str, Any] | None = None,
    ) -> BaseModel:
        raise LLMUnavailableError(
            "real LLM provider integration is out of scope for Package B; "
            "no network call will be made",
            provider=self.config.provider,
        )


def _strip_code_fence(text: str) -> str:
    """Remove a Markdown JSON code fence (```json ... ```) around output."""
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


class OpenAICompatibleLLMClient:
    """Real OpenAI-compatible ``chat/completions`` provider (FR-002/FR-003).

    Network-gated by construction: this class is only returned by
    ``resolve_llm_client`` when ``allow_network=True`` and key / base URL /
    model are all present. It implements the ``StructuredLLMClient``
    boundary: ``generate_structured(messages, output_schema, metadata)``
    returns a validated ``output_schema`` instance.

    Request control (FR-003):

    - configured ``timeout_seconds`` is passed to the HTTP layer;
    - HTTP 429 and 5xx are retried with exponential backoff up to
      ``max_retries``; other 4xx (401/403/...) fail immediately;
    - a simple RPM limiter enforces a minimum inter-request interval of
      ``60 / rate_limit_rpm`` seconds.

    Error mapping: non-JSON body / schema mismatch -> ``LLMInvalidOutputError``
    (``llm_invalid_output``); HTTP error / timeout / exhausted retries ->
    ``LLMRequestError`` (``llm_request_failed``). System failures are never
    disguised as ``not_found``. Every error message is redacted: the API key
    never appears in errors, logs, manifests or records.

    Tests inject ``httpx.MockTransport`` via ``transport`` and never touch the
    network.
    """

    is_fake = False

    def __init__(self, config: LLMConfig, *, transport: Any | None = None):
        self.config = config
        self.transport = transport
        self._last_request_at = 0.0
        self.request_count = 0

    @property
    def provider_name(self) -> str:
        return self.config.provider or REAL_PROVIDER

    @property
    def model_name(self) -> str:
        return self.config.model or "unknown"

    def _redact(self, text: str) -> str:
        key = self.config.api_key
        if key:
            text = text.replace(key, "***")
        return text

    def _endpoint_url(self) -> str:
        base = (self.config.base_url or "").rstrip("/")
        return f"{base}/chat/completions"

    def _throttle(self) -> None:
        """Enforce the RPM limit: sleep so requests are at least
        ``60 / rate_limit_rpm`` seconds apart (no-op for unlimited)."""
        rpm = self.config.rate_limit_rpm
        if rpm <= 0:
            return
        interval = 60.0 / rpm
        now = time.monotonic()
        wait = interval - (now - self._last_request_at)
        if wait > 0:
            time.sleep(wait)
        self._last_request_at = time.monotonic()

    def _send_once(self, url: str, headers: dict, payload: dict) -> Any:
        """One HTTP attempt. HTTP transport errors become ``LLMRequestError``
        with a redacted message; the response object is returned raw."""
        import httpx

        self._throttle()
        timeout = httpx.Timeout(self.config.timeout_seconds)
        if self.transport is not None:
            client = httpx.Client(timeout=timeout, transport=self.transport)
        else:
            client = httpx.Client(timeout=timeout)
        try:
            return client.post(url, headers=headers, json=payload)
        except httpx.HTTPError as exc:
            raise LLMRequestError(
                self._redact(
                    f"LLM request failed: {type(exc).__name__}; "
                    "the configured provider is unreachable or timed out "
                    f"(timeout={self.config.timeout_seconds}s)"
                )
            ) from exc
        finally:
            client.close()

    def _parse_response(
        self, response: Any, output_schema: type[BaseModel]
    ) -> BaseModel:
        """Parse a 200 response into a validated ``output_schema`` instance."""
        try:
            data = response.json()
        except (ValueError, TypeError) as exc:
            raise LLMInvalidOutputError(
                self._redact(f"LLM response body is not valid JSON: {exc}")
            ) from exc
        if not isinstance(data, dict):
            raise LLMInvalidOutputError("LLM response must be a JSON object")
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise LLMInvalidOutputError("LLM response contains no choices")
        message = choices[0].get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str) or not content.strip():
            raise LLMInvalidOutputError("LLM response message has no content")
        text = _strip_code_fence(content)
        try:
            parsed = json.loads(text)
        except (ValueError, TypeError) as exc:
            raise LLMInvalidOutputError(
                self._redact(f"LLM output is not valid JSON: {exc}")
            ) from exc
        if not isinstance(parsed, dict):
            raise LLMInvalidOutputError("LLM output must be a JSON object")
        try:
            return output_schema.model_validate(parsed)
        except (ValidationError, TypeError, ValueError) as exc:
            raise LLMInvalidOutputError(
                self._redact(
                    f"LLM output failed the expected schema validation: {exc}"
                )
            ) from exc

    def generate_structured(
        self,
        messages: list[dict[str, Any]],
        output_schema: type[BaseModel],
        metadata: dict[str, Any] | None = None,
    ) -> BaseModel:
        url = self._endpoint_url()
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.config.model,
            "messages": messages,
            "response_format": {"type": "json_object"},
        }
        for attempt in range(self.config.max_retries + 1):
            response = self._send_once(url, headers, payload)
            if response.status_code == 200:
                return self._parse_response(response, output_schema)
            retryable = response.status_code == 429 or response.status_code >= 500
            if retryable and attempt < self.config.max_retries:
                time.sleep(0.5 * (2 ** attempt))
                continue
            raise LLMRequestError(
                self._redact(
                    f"LLM provider returned HTTP {response.status_code}"
                ),
                status_code=response.status_code,
            )
        # Unreachable: the loop either returns or raises.
        raise LLMRequestError("LLM request failed after retries")


def resolve_llm_client(config: LLMConfig | None = None) -> StructuredLLMClient:
    """Resolve the configured client.

    Default (no config / ``provider="fake"``) returns the offline
    ``FakeLLMProvider``. A non-fake provider without explicit network
    permission raises ``LLMUnavailableError`` at resolution time. With
    ``allow_network`` set, provider ``openai_compatible`` is validated
    (key / base URL / model must all be present, checked one at a time) and
    resolved to the real ``OpenAICompatibleLLMClient``; every other provider
    name still returns a ``RealLLMClientStub`` whose calls fail explicitly
    (Package B scope).
    """
    config = config or LLMConfig.from_env()
    provider = (config.provider or "fake").strip().lower()
    if provider in ("", "fake"):
        return FakeLLMProvider()
    if not config.allow_network:
        raise LLMUnavailableError(
            f"LLM provider {config.provider!r} requires network permission; "
            "set TRANSIT_SCHOLAR_LLM_ALLOW_NETWORK=1 explicitly (default "
            "behavior is offline)",
            provider=config.provider,
        )
    if provider == REAL_PROVIDER:
        missing = [name for name in ("api_key", "base_url", "model")
                   if not getattr(config, name)]
        if missing:
            field = missing[0]
            env_name = {
                "api_key": _ENV_API_KEY,
                "base_url": _ENV_BASE_URL,
                "model": _ENV_MODEL,
            }[field]
            raise LLMUnavailableError(
                f"LLM provider {config.provider!r} is missing required "
                f"config {field!r}; set {env_name} in the environment before "
                "enabling real network calls",
                provider=config.provider,
            )
        return OpenAICompatibleLLMClient(config)
    return RealLLMClientStub(config)


class FakeCallRecord(BaseModel):
    """Recorded fake-provider invocation (AC-L2S2B-02)."""

    prompt_key: str | None = None
    messages: list[dict[str, Any]]
    metadata: dict[str, Any] = {}
    output: dict[str, Any] | None = None
    outcome: str = "ok"
