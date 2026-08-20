"""Injectable retrieval boundary for the extraction engine (FR-B-004).

The engine talks to retrieval only through ``RetrievalBoundary``. ``FakeRetrieval``
is the deterministic offline default used by tests; ``HybridRetrievalWrapper``
is the production wrapper around ``search_hybrid`` with a lazy import so the
L2S1 retrieval stack is never imported by the deterministic package import
path. L2S1 types are referenced only under ``TYPE_CHECKING``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

from .errors import RetrievalUnavailableError

if TYPE_CHECKING:
    from transit_scholar.layer2.schema import RetrievalResult


class RetrievalBoundary(Protocol):
    """Retrieval boundary returning an L2S1 ``RetrievalResult`` envelope."""

    def retrieve(self, paper_id: str, query: str, top_k: int) -> "RetrievalResult":
        ...


class FakeRetrieval:
    """Deterministic offline retrieval (FR-B-004, AC-L2S2B-06).

    Responses are canned ``RetrievalResult`` objects keyed by ``(paper_id,
    query)`` tuple or bare query string. Unconfigured keys return an ``ok``
    result with no hits. Every call is recorded in ``calls``.
    """

    def __init__(self, responses: dict[Any, Any] | None = None):
        self.responses: dict[Any, Any] = dict(responses or {})
        self.calls: list[dict[str, Any]] = []

    def retrieve(self, paper_id: str, query: str, top_k: int) -> Any:
        self.calls.append({"paper_id": paper_id, "query": query, "top_k": top_k})
        for key in ((paper_id, query), query):
            if key in self.responses:
                return self.responses[key]
        return self._empty(paper_id)

    def _empty(self, paper_id: str) -> Any:
        from transit_scholar.layer2.schema import RetrievalResult

        return RetrievalResult(status="ok", method="fake", hits=[])


class HybridRetrievalWrapper:
    """Production wrapper around ``search_hybrid`` (AC-L2S2B-06).

    The import of the L2S1 retrieval API happens lazily inside ``retrieve``,
    so merely importing this module never pulls in the retrieval stack.
    Deterministic Package B tests never exercise this wrapper.
    """

    def __init__(self, top_k: int = 8, rerank: bool = True):
        self.top_k = int(top_k)
        self.rerank = bool(rerank)

    def retrieve(self, paper_id: str, query: str, top_k: int | None = None) -> Any:
        from transit_scholar.layer2.retrieval.api import search_hybrid

        try:
            return search_hybrid(
                paper_id,
                query,
                top_k=top_k if top_k is not None else self.top_k,
                rerank=self.rerank,
            )
        except RetrievalUnavailableError:
            raise
        except Exception as exc:  # noqa: BLE001 - boundary failure is explicit
            raise RetrievalUnavailableError(
                f"search_hybrid failed for paper {paper_id!r}: "
                f"{type(exc).__name__}: {exc}"
            ) from exc
