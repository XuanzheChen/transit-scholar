"""Evaluation runner comparing four retrieval variants (FR-014).

Variants: BM25 only, Dense only, Hybrid RRF, Hybrid RRF + Reranker. Metrics are
computed per gold query and averaged per variant. ``search_fn`` is injected so
tests can drive the deterministic fake backend.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Sequence

from transit_scholar.layer2.schema import GoldQuery, RetrievalHit

VARIANTS = ("bm25", "dense", "hybrid", "hybrid_rerank")

SearchFn = Callable[[str, str], Sequence[RetrievalHit]]


@dataclass
class EvaluationReport:
    paper_id: str
    query_count: int
    variants: dict[str, dict[str, dict[str, float]]] = field(default_factory=dict)
    summary: dict[str, dict[str, float]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "paper_id": self.paper_id,
            "query_count": self.query_count,
            "variants": self.variants,
            "summary": self.summary,
        }


def _gold_match(hit: RetrievalHit, gold_ids: set[str]) -> set[str]:
    return {ref.block_id for ref in hit.source_refs if ref.block_id in gold_ids}


def metrics_from_hits(hits: Sequence[RetrievalHit], gold_ids: Sequence[str]) -> dict[str, float]:
    """Ranking metrics for a hit list using coverage of gold block ids."""
    gold_set = set(gold_ids)
    if not gold_set:
        return {"recall@5": 0.0, "recall@10": 0.0, "mrr@10": 0.0, "ndcg@10": 0.0}
    covered: set[str] = set()
    cumulative: list[float] = []
    for hit in hits:
        covered |= _gold_match(hit, gold_set)
        cumulative.append(len(covered) / len(gold_set))

    def coverage_at(k: int) -> float:
        if k <= 0:
            return 0.0
        return cumulative[min(k, len(cumulative)) - 1] if cumulative else 0.0

    mrr = 0.0
    for rank, hit in enumerate(hits[:10], start=1):
        if _gold_match(hit, gold_set):
            mrr = 1.0 / rank
            break

    dcg = 0.0
    rank = 1
    running: set[str] = set()
    import math

    for hit in hits[:10]:
        new = _gold_match(hit, gold_set) - running
        running |= _gold_match(hit, gold_set)
        gain = len(new) / len(gold_set)
        dcg += gain / math.log2(rank + 1)
        rank += 1
    ideal_ranks = min(10, len(gold_set))
    idcg = sum(1.0 / math.log2(r + 1) for r in range(1, ideal_ranks + 1))
    ndcg = dcg / idcg if idcg else 0.0

    return {
        "recall@5": coverage_at(5),
        "recall@10": coverage_at(10),
        "mrr@10": mrr,
        "ndcg@10": ndcg,
    }


def evaluate_paper(
    paper_id: str,
    gold_queries: Sequence[GoldQuery],
    *,
    search_fn: SearchFn,
    variants: Sequence[str] = VARIANTS,
) -> EvaluationReport:
    """Run every variant over the gold queries and aggregate metrics."""
    report = EvaluationReport(paper_id=paper_id, query_count=len(gold_queries))
    for variant in variants:
        per_query: dict[str, dict[str, float]] = {}
        for query in gold_queries:
            hits = search_fn(query.query, variant)
            per_query[query.query] = metrics_from_hits(hits, query.gold_block_ids)
        report.variants[variant] = per_query
        report.summary[variant] = _mean_metrics(per_query.values())
    return report


def _mean_metrics(entries) -> dict[str, float]:
    keys = ("recall@5", "recall@10", "mrr@10", "ndcg@10")
    result: dict[str, float] = {}
    for key in keys:
        values = [entry[key] for entry in entries]
        result[key] = sum(values) / len(values) if values else 0.0
    return result
