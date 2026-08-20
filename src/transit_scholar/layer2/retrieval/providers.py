"""Embedding / reranker provider boundary (FR-010).

Production path is a cloud provider adapter -- never a locally loaded model.
When the API key is unset, provider dependencies are missing, or network is
explicitly blocked, the resolved provider is ``unavailable`` with a structured
reason; the retrieval boundary then returns an explicit
``unavailable``/``dependency_missing`` result instead of a fake success. Keys
are read via the existing ``config.py`` dotenv boundary and are never written
to manifests, logs, results or errors.
"""

from __future__ import annotations

import abc
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from transit_scholar.layer2.config import Layer2Config

#: The cloud API revision is recorded without pretending that a provider-managed
#: model has a locally pinned weight revision.
EMBEDDING_MODEL_REVISION = "api-v1"
RERANKER_MODEL_REVISION = "api-v1"

_RETRYABLE_HTTP_STATUS = frozenset({429, 500, 503, 504})
_MAX_REQUEST_ATTEMPTS = 5
_BASE_RETRY_DELAY_SECONDS = 5.0
_MAX_RETRY_DELAY_SECONDS = 60.0


class UnavailableError(RuntimeError):
    """Raised by providers when a capability is structurally unavailable."""

    def __init__(self, reason: str, error_code: str = "unavailable") -> None:
        super().__init__(reason)
        self.reason = reason
        self.error_code = error_code


@dataclass
class ProviderInfo:
    provider: str
    model: str
    dimension: int | None
    revision: str = ""


class EmbeddingProvider(abc.ABC):
    """Abstract document/query embedding provider."""

    @property
    @abc.abstractmethod
    def available(self) -> bool:
        ...

    @property
    @abc.abstractmethod
    def reason(self) -> str | None:
        ...

    @property
    @abc.abstractmethod
    def info(self) -> ProviderInfo | None:
        ...

    @abc.abstractmethod
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        ...

    @abc.abstractmethod
    def embed_query(self, text: str) -> list[float]:
        ...

    @abc.abstractmethod
    def dimension(self) -> int | None:
        ...


class RerankerProvider(abc.ABC):
    """Abstract query-document reranker provider."""

    @property
    @abc.abstractmethod
    def available(self) -> bool:
        ...

    @property
    @abc.abstractmethod
    def reason(self) -> str | None:
        ...

    @property
    @abc.abstractmethod
    def info(self) -> ProviderInfo | None:
        ...

    @abc.abstractmethod
    def rerank(self, query: str, documents: list[str], top_k: int) -> list[tuple[int, float]]:
        ...


class UnavailableEmbeddingProvider(EmbeddingProvider):
    """Embedding provider that reports a structured unavailable status."""

    def __init__(self, reason: str, *, error_code: str = "unavailable") -> None:
        self._reason = reason
        self._error_code = error_code
        self._info = None

    @property
    def available(self) -> bool:
        return False

    @property
    def reason(self) -> str | None:
        return self._reason

    @property
    def info(self) -> ProviderInfo | None:
        return self._info

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        raise UnavailableError(self._reason, error_code=self._error_code)

    def embed_query(self, text: str) -> list[float]:
        raise UnavailableError(self._reason, error_code=self._error_code)

    def dimension(self) -> int | None:
        return None


class UnavailableRerankerProvider(RerankerProvider):
    """Reranker provider that reports a structured unavailable status."""

    def __init__(self, reason: str, *, error_code: str = "unavailable") -> None:
        self._reason = reason
        self._error_code = error_code

    @property
    def available(self) -> bool:
        return False

    @property
    def reason(self) -> str | None:
        return self._reason

    @property
    def info(self) -> ProviderInfo | None:
        return None

    def rerank(self, query: str, documents: list[str], top_k: int) -> list[tuple[int, float]]:
        raise UnavailableError(self._reason, error_code=self._error_code)


class CloudEmbeddingProvider(EmbeddingProvider):
    """OpenAI-compatible cloud embedding adapter (production path).

    Only instantiated when a key is configured and network is not blocked. The
    adapter performs an HTTP POST to the provider's ``/v1/embeddings`` endpoint
    for documents and ``/v1/embeddings`` for the query with the model's
    recommended instruction prefix. The key is held only in memory.
    """

    def __init__(
        self,
        *,
        provider: str,
        api_key: str,
        model: str,
        dimension: int,
        base_url: str,
        block_network: bool,
    ) -> None:
        self._provider = provider
        self._api_key = api_key
        self._model = model
        self._dimension = dimension
        self._base_url = base_url
        self._block_network = block_network
        self._info = ProviderInfo(
            provider=provider,
            model=model,
            dimension=dimension,
            revision=EMBEDDING_MODEL_REVISION,
        )

    @property
    def available(self) -> bool:
        return not self._block_network

    @property
    def reason(self) -> str | None:
        return "network_blocked" if self._block_network else None

    @property
    def info(self) -> ProviderInfo | None:
        return self._info

    def dimension(self) -> int | None:
        return self._dimension

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._call(texts, task="retrieval.passage")

    def embed_query(self, text: str) -> list[float]:
        return self._call([text], task="retrieval.query")[0]

    def _call(self, texts: list[str], *, task: str) -> list[list[float]]:
        if self._block_network:
            raise UnavailableError("network_blocked", error_code="network_blocked")
        url = f"{self._base_url}/embeddings"
        payload: dict[str, Any] = {"model": self._model, "input": texts}
        if self._provider == "jina":
            payload.update(
                {
                    "task": task,
                    "dimensions": self._dimension,
                    "normalized": True,
                }
            )
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._api_key}",
            },
            method="POST",
        )
        body = _post_json_with_retry(
            request,
            capability="embedding",
            api_key=self._api_key,
        )
        try:
            data = body["data"]
            indexes = [int(item["index"]) for item in data]
            if sorted(indexes) != list(range(len(texts))):
                raise ValueError("embedding indexes do not match inputs")
            ordered = sorted(data, key=lambda item: int(item["index"]))
            vectors = [list(map(float, item["embedding"])) for item in ordered]
        except (KeyError, TypeError, ValueError) as exc:
            raise UnavailableError(
                "embedding provider returned an invalid response",
                error_code="provider_response_invalid",
            ) from exc
        if len(vectors) != len(texts):
            raise UnavailableError(
                "embedding provider returned an unexpected vector count",
                error_code="provider_response_invalid",
            )
        if any(len(vector) != self._dimension for vector in vectors):
            raise UnavailableError(
                "embedding provider returned an unexpected vector dimension",
                error_code="provider_response_invalid",
            )
        return vectors


class CloudRerankerProvider(RerankerProvider):
    """OpenAI-compatible cloud reranker adapter (production path)."""

    def __init__(
        self,
        *,
        provider: str,
        api_key: str,
        model: str,
        base_url: str,
        block_network: bool,
    ) -> None:
        self._provider = provider
        self._api_key = api_key
        self._model = model
        self._base_url = base_url
        self._block_network = block_network
        self._info = ProviderInfo(
            provider=provider,
            model=model,
            dimension=None,
            revision=RERANKER_MODEL_REVISION,
        )

    @property
    def available(self) -> bool:
        return not self._block_network

    @property
    def reason(self) -> str | None:
        return "network_blocked" if self._block_network else None

    @property
    def info(self) -> ProviderInfo | None:
        return self._info

    def rerank(self, query: str, documents: list[str], top_k: int) -> list[tuple[int, float]]:
        if self._block_network:
            raise UnavailableError("network_blocked", error_code="network_blocked")
        url = f"{self._base_url}/rerank"
        payload = {
            "model": self._model,
            "query": query,
            "documents": documents,
            "top_n": min(max(0, top_k), len(documents)),
            "return_documents": False,
        }
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._api_key}",
            },
            method="POST",
        )
        body = _post_json_with_retry(
            request,
            capability="rerank",
            api_key=self._api_key,
        )
        try:
            results = body.get("results", body.get("data", []))
            ranked = [
                (int(item["index"]), float(item["relevance_score"]))
                for item in results
            ]
        except (KeyError, TypeError, ValueError) as exc:
            raise UnavailableError(
                "reranker provider returned an invalid response",
                error_code="provider_response_invalid",
            ) from exc
        if any(index < 0 or index >= len(documents) for index, _ in ranked):
            raise UnavailableError(
                "reranker provider returned an invalid document index",
                error_code="provider_response_invalid",
            )
        return ranked[:top_k]


def _provider_base_url(provider: str) -> str:
    return {
        "jina": "https://api.jina.ai/v1",
        "dashscope": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    }.get(
        provider, f"https://{provider}"
    )


def _safe_provider_error(capability: str, exc: Exception, api_key: str) -> str:
    """Return a useful provider error without ever exposing the credential."""
    detail = str(exc).replace(api_key, "[REDACTED]") if api_key else str(exc)
    return f"{capability} request failed: {detail}"


def _post_json_with_retry(
    request: urllib.request.Request,
    *,
    capability: str,
    api_key: str,
) -> dict[str, Any]:
    """POST JSON with bounded retry for transient provider failures.

    Jina enforces request, token and concurrency limits. A 429 can therefore
    occur during an otherwise valid batch. Respect ``Retry-After`` when the
    provider sends it; otherwise use deterministic exponential backoff. The
    retry is deliberately bounded so a persistent outage becomes a structured
    retrieval result instead of blocking indefinitely.
    """
    for attempt in range(_MAX_REQUEST_ATTEMPTS):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("provider response is not a JSON object")
                return payload
        except urllib.error.HTTPError as exc:
            if exc.code in _RETRYABLE_HTTP_STATUS and attempt + 1 < _MAX_REQUEST_ATTEMPTS:
                time.sleep(_retry_delay_seconds(exc, attempt))
                continue
            error_code = "rate_limited" if exc.code == 429 else "provider_error"
            raise UnavailableError(
                _safe_provider_error(capability, exc, api_key),
                error_code=error_code,
            ) from exc
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise UnavailableError(
                _safe_provider_error(capability, exc, api_key),
                error_code="provider_error",
            ) from exc
    raise AssertionError("unreachable provider retry state")


def _retry_delay_seconds(exc: urllib.error.HTTPError, attempt: int) -> float:
    retry_after = exc.headers.get("Retry-After") if exc.headers is not None else None
    if retry_after:
        try:
            return min(_MAX_RETRY_DELAY_SECONDS, max(0.0, float(retry_after)))
        except ValueError:
            pass
    return min(
        _MAX_RETRY_DELAY_SECONDS,
        _BASE_RETRY_DELAY_SECONDS * (2**attempt),
    )


def resolve_embedding_provider(config: Layer2Config) -> EmbeddingProvider:
    """Resolve the configured cloud embedding provider or an unavailable stub."""
    provider = config.resolved_embedding_provider
    api_key = config.embedding_api_key or (
        config.jina_api_key if provider == "jina" else None
    )
    if not api_key:
        return UnavailableEmbeddingProvider(
            "missing_api_key", error_code="missing_api_key"
        )
    if config.block_network or not config.retrieval_allow_network:
        return UnavailableEmbeddingProvider(
            "network_blocked", error_code="network_blocked"
        )
    model = config.embedding_model or config.embedding_model_default
    dimension = config.embedding_dimension or config.embedding_dimension_default
    return CloudEmbeddingProvider(
        provider=provider,
        api_key=api_key,
        model=model,
        dimension=dimension,
        base_url=_provider_base_url(provider),
        block_network=config.block_network,
    )


def resolve_reranker_provider(config: Layer2Config) -> RerankerProvider:
    """Resolve the configured cloud reranker provider or an unavailable stub."""
    provider = config.resolved_reranker_provider
    api_key = config.reranker_api_key or (
        config.jina_api_key if provider == "jina" else None
    )
    if not api_key:
        return UnavailableRerankerProvider(
            "missing_api_key", error_code="missing_api_key"
        )
    if config.block_network or not config.retrieval_allow_network:
        return UnavailableRerankerProvider(
            "network_blocked", error_code="network_blocked"
        )
    model = config.reranker_model or config.reranker_model_default
    return CloudRerankerProvider(
        provider=provider,
        api_key=api_key,
        model=model,
        base_url=_provider_base_url(provider),
        block_network=config.block_network,
    )
