"""Deterministic contract tests for the Jina cloud retrieval adapters."""

from __future__ import annotations

import json
from email.message import Message
import urllib.error

import pytest

from transit_scholar.layer2.retrieval.providers import (
    CloudEmbeddingProvider,
    CloudRerankerProvider,
    UnavailableError,
)


class _Response:
    def __init__(self, payload: dict) -> None:
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return self._body


def _embedding(dimension: int = 4) -> CloudEmbeddingProvider:
    return CloudEmbeddingProvider(
        provider="jina",
        api_key="jina-secret",
        model="jina-embeddings-v3",
        dimension=dimension,
        base_url="https://api.jina.ai/v1",
        block_network=False,
    )


def test_jina_embedding_uses_asymmetric_tasks_and_restores_input_order(monkeypatch):
    calls: list[dict] = []

    def fake_urlopen(request, timeout):
        assert timeout == 30
        assert request.full_url == "https://api.jina.ai/v1/embeddings"
        assert request.headers["Authorization"] == "Bearer jina-secret"
        payload = json.loads(request.data)
        calls.append(payload)
        if len(payload["input"]) == 2:
            return _Response(
                {
                    "data": [
                        {"index": 1, "embedding": [0, 1, 0, 0]},
                        {"index": 0, "embedding": [1, 0, 0, 0]},
                    ]
                }
            )
        return _Response({"data": [{"index": 0, "embedding": [0, 0, 1, 0]}]})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    provider = _embedding()
    assert provider.embed_documents(["doc-a", "doc-b"]) == [
        [1.0, 0.0, 0.0, 0.0],
        [0.0, 1.0, 0.0, 0.0],
    ]
    assert provider.embed_query("query") == [0.0, 0.0, 1.0, 0.0]
    assert calls[0] == {
        "model": "jina-embeddings-v3",
        "input": ["doc-a", "doc-b"],
        "task": "retrieval.passage",
        "dimensions": 4,
        "normalized": True,
    }
    assert calls[1]["task"] == "retrieval.query"


@pytest.mark.parametrize(
    "response",
    [
        {"data": [{"index": 0, "embedding": [1.0, 0.0]}]},
        {"data": []},
        {
            "data": [
                {"index": 0, "embedding": [1.0, 0.0, 0.0, 0.0]},
                {"index": 0, "embedding": [0.0, 1.0, 0.0, 0.0]},
            ]
        },
        {"unexpected": []},
    ],
)
def test_jina_embedding_rejects_bad_shape(monkeypatch, response):
    monkeypatch.setattr("urllib.request.urlopen", lambda *_a, **_k: _Response(response))
    with pytest.raises(UnavailableError) as exc_info:
        texts = ["one", "two"] if len(response.get("data", [])) == 2 else ["one"]
        _embedding().embed_documents(texts)
    assert exc_info.value.error_code == "provider_response_invalid"


def test_jina_reranker_request_and_response(monkeypatch):
    captured: dict = {}

    def fake_urlopen(request, timeout):
        captured.update(json.loads(request.data))
        return _Response(
            {
                "results": [
                    {"index": 2, "relevance_score": 0.9},
                    {"index": 0, "relevance_score": 0.7},
                ]
            }
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    provider = CloudRerankerProvider(
        provider="jina",
        api_key="jina-secret",
        model="jina-reranker-v3",
        base_url="https://api.jina.ai/v1",
        block_network=False,
    )
    assert provider.rerank("query", ["a", "b", "c"], 2) == [(2, 0.9), (0, 0.7)]
    assert captured == {
        "model": "jina-reranker-v3",
        "query": "query",
        "documents": ["a", "b", "c"],
        "top_n": 2,
        "return_documents": False,
    }


def test_provider_network_error_redacts_api_key(monkeypatch):
    secret = "jina-super-secret"

    def fail(*_args, **_kwargs):
        raise urllib.error.URLError(f"request rejected for {secret}")

    monkeypatch.setattr("urllib.request.urlopen", fail)
    provider = CloudEmbeddingProvider(
        provider="jina",
        api_key=secret,
        model="jina-embeddings-v3",
        dimension=4,
        base_url="https://api.jina.ai/v1",
        block_network=False,
    )
    with pytest.raises(UnavailableError) as exc_info:
        provider.embed_query("query")
    assert secret not in str(exc_info.value)
    assert "[REDACTED]" in str(exc_info.value)


def test_jina_retries_429_using_retry_after(monkeypatch):
    attempts = {"count": 0}
    sleeps: list[float] = []

    def rate_limited_then_ok(request, timeout):
        attempts["count"] += 1
        if attempts["count"] == 1:
            headers = Message()
            headers["Retry-After"] = "0.25"
            raise urllib.error.HTTPError(
                request.full_url,
                429,
                "Too Many Requests",
                headers,
                None,
            )
        return _Response({"data": [{"index": 0, "embedding": [1, 0, 0, 0]}]})

    monkeypatch.setattr("urllib.request.urlopen", rate_limited_then_ok)
    monkeypatch.setattr("transit_scholar.layer2.retrieval.providers.time.sleep", sleeps.append)

    assert _embedding().embed_query("query") == [1.0, 0.0, 0.0, 0.0]
    assert attempts["count"] == 2
    assert sleeps == [0.25]


def test_jina_exhausted_429_is_structured(monkeypatch):
    sleeps: list[float] = []

    def always_limited(request, timeout):
        raise urllib.error.HTTPError(
            request.full_url,
            429,
            "Too Many Requests",
            Message(),
            None,
        )

    monkeypatch.setattr("urllib.request.urlopen", always_limited)
    monkeypatch.setattr("transit_scholar.layer2.retrieval.providers.time.sleep", sleeps.append)

    with pytest.raises(UnavailableError) as exc_info:
        _embedding().embed_query("query")
    assert exc_info.value.error_code == "rate_limited"
    assert sleeps == [5.0, 10.0, 20.0, 40.0]
