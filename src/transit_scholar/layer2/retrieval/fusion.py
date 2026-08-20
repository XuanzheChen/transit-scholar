"""Hybrid fusion: BM25 + Dense -> RRF -> optional rerank (FR-010/FR-011).

Reranking operates on a *protected set*: the RRF top-``final_top_k`` list that
is also returned when ``rerank=False``. The reranker reorders only those
members, and any protected member missing from the reranker output is padded
back in RRF order, so ``set(hybrid) == set(hybrid_rerank)`` by construction
and recall is never reduced by the rerank step. A transient reranker provider
failure degrades to the plain RRF top-k with a structured warning instead of
failing the query.
"""

from __future__ import annotations

from typing import Callable

from transit_scholar.layer2.config import Layer2Config
from transit_scholar.layer2.retrieval.providers import RerankerProvider, UnavailableError
from transit_scholar.layer2.schema import RetrievalHit

_RRF_K = 60


def fuse_hybrid(
    query: str,
    bm25_hits: list[RetrievalHit],
    dense_hits: list[RetrievalHit],
    config: Layer2Config,
    *,
    reranker_provider: RerankerProvider | None = None,
    rerank: bool = False,
    top_k: int | None = None,
) -> tuple[list[RetrievalHit], list[str]]:
    """Weighted RRF fusion of BM25 + dense candidates, optionally reranked.

    ``bm25_rank`` / ``dense_rank`` carry each method's own 1-based rank,
    ``rrf_rank`` is the fused rank, and ``rerank_score`` is populated when a
    rerank step ran. Returns ``(hits, warnings)`` where ``hits`` contains at
    most ``final_top_k`` hits. ``warnings`` records structured (desensitized)
    degradation facts, e.g. a reranker provider failure that fell back to the
    RRF top-k.
    """
    candidate_k = config.fusion_candidate_k
    final_top_k = top_k if top_k is not None else config.final_top_k
    bm25_weight = max(0.0, config.rrf_bm25_weight)
    dense_weight = max(0.0, config.rrf_dense_weight)

    scores: dict[str, float] = {}
    ranks: dict[str, dict[str, int]] = {}
    for rank, hit in enumerate(bm25_hits, start=1):
        key = hit.chunk_id or ""
        scores[key] = scores.get(key, 0.0) + bm25_weight * 1.0 / (_RRF_K + rank)
        ranks.setdefault(key, {})["bm25"] = rank
    for rank, hit in enumerate(dense_hits, start=1):
        key = hit.chunk_id or ""
        scores[key] = scores.get(key, 0.0) + dense_weight * 1.0 / (_RRF_K + rank)
        ranks.setdefault(key, {})["dense"] = rank

    by_id: dict[str, RetrievalHit] = {}
    for hit in [*bm25_hits, *dense_hits]:
        if hit.chunk_id is not None:
            by_id.setdefault(hit.chunk_id, hit)

    fused = sorted(scores.items(), key=lambda pair: pair[1], reverse=True)[:candidate_k]
    rrf_hits: list[RetrievalHit] = []
    for rrf_rank, (chunk_id, score) in enumerate(fused, start=1):
        base = by_id[chunk_id]
        rank_info = ranks[chunk_id]
        rrf_hits.append(
            RetrievalHit(
                paper_id=base.paper_id,
                chunk_id=base.chunk_id,
                score=score,
                retrieval_method="hybrid",
                section_path=list(base.section_path),
                pages=list(base.pages),
                source_refs=list(base.source_refs),
                text=base.text,
                rank=0,
                bm25_rank=rank_info.get("bm25"),
                dense_rank=rank_info.get("dense"),
                rrf_rank=rrf_rank,
                rerank_score=None,
            )
        )

    #: Protected set: the exact list returned by the plain hybrid path.
    protected = rrf_hits[:final_top_k]

    warnings: list[str] = []
    if rerank and reranker_provider is not None and reranker_provider.available:
        try:
            reranked = _apply_rerank(
                query, protected, reranker_provider, top_k=final_top_k
            )
        except UnavailableError as exc:
            # Provider call / output conversion failed: degrade to the RRF
            # top-k with a structured, already-desensitized reason.
            warnings.append(
                f"rerank failed ({exc.error_code}): {exc.reason}; "
                "falling back to RRF hybrid top-k"
            )
        else:
            final_hits = _pad_protected_set(reranked, protected, final_top_k)
            for rank, hit in enumerate(final_hits, start=1):
                hit.rank = rank
            return final_hits, warnings

    for rank, hit in enumerate(protected, start=1):
        hit.rank = rank
    return protected, warnings


def _apply_rerank(
    query: str,
    protected_hits: list[RetrievalHit],
    reranker_provider: RerankerProvider,
    *,
    top_k: int,
) -> list[RetrievalHit]:
    """Rerank the protected set and convert provider output to hits.

    Exception handling is scoped to the external provider call and the
    conversion of its output; unrelated programming errors in hit cloning are
    not swallowed here.
    """
    documents = [_retrieval_text_for_hit(hit) for hit in protected_hits]
    try:
        scored = reranker_provider.rerank(query, documents, top_k)
    except UnavailableError:
        raise
    except Exception as exc:  # noqa: BLE001 - provider failure is structured
        raise UnavailableError(
            f"rerank request failed: {type(exc).__name__}: {exc}",
            error_code="provider_error",
        ) from exc
    try:
        index_map = {index: hit for index, hit in enumerate(protected_hits)}
        ordered: list[RetrievalHit] = []
        for index, score in scored:
            hit = index_map.get(index)
            if hit is None:
                continue
            clone = RetrievalHit(
                paper_id=hit.paper_id,
                chunk_id=hit.chunk_id,
                score=hit.score,
                retrieval_method=hit.retrieval_method,
                section_path=list(hit.section_path),
                pages=list(hit.pages),
                source_refs=list(hit.source_refs),
                text=hit.text,
                rank=0,
                bm25_rank=hit.bm25_rank,
                dense_rank=hit.dense_rank,
                rrf_rank=hit.rrf_rank,
                rerank_score=float(score),
            )
            ordered.append(clone)
    except (KeyError, TypeError, ValueError, IndexError) as exc:
        raise UnavailableError(
            "reranker provider returned an invalid response",
            error_code="provider_response_invalid",
        ) from exc
    return ordered


def _pad_protected_set(
    reranked: list[RetrievalHit],
    protected_hits: list[RetrievalHit],
    top_k: int,
) -> list[RetrievalHit]:
    """Pad a short rerank output back to the full protected set (RRF order).

    Guarantees ``set(result) == set(protected_hits)`` even when the provider
    returns fewer items than requested, so reranking can never drop a
    protected member from the final top-k.
    """
    by_id = {hit.chunk_id: hit for hit in reranked if hit.chunk_id is not None}
    final_hits = list(reranked[:top_k])
    for hit in protected_hits:
        if hit.chunk_id is not None and hit.chunk_id not in by_id:
            final_hits.append(hit)
    return final_hits[:top_k]


def _retrieval_text_for_hit(hit: RetrievalHit) -> str:
    prefix = " > ".join(hit.section_path)
    if prefix:
        return prefix + "\n\n" + hit.text
    return hit.text
