"""Ranking metrics: Recall@k, MRR@k, nDCG@k (FR-014)."""

from __future__ import annotations

import math
from typing import Iterable


def recall_at_k(ranking: list[str], gold: Iterable[str], k: int) -> float:
    """Fraction of gold block ids found among the top ``k`` ranked items."""
    gold_set = set(gold)
    if not gold_set:
        return 0.0
    top_k = ranking[:k]
    hits = sum(1 for item in top_k if item in gold_set)
    return hits / len(gold_set)


def mrr_at_k(ranking: list[str], gold: Iterable[str], k: int) -> float:
    """Reciprocal rank of the first gold hit within the top ``k``, else 0."""
    gold_set = set(gold)
    for rank, item in enumerate(ranking[:k], start=1):
        if item in gold_set:
            return 1.0 / rank
    return 0.0


def ndcg_at_k(ranking: list[str], gold: Iterable[str], k: int) -> float:
    """nDCG@k over binary gold relevance (graded 1 for gold ids)."""
    gold_set = set(gold)
    if not gold_set:
        return 0.0
    top_k = ranking[:k]
    if not top_k:
        return 0.0
    dcg = 0.0
    for rank, item in enumerate(top_k, start=1):
        rel = 1.0 if item in gold_set else 0.0
        dcg += (2**rel - 1) / math.log2(rank + 1)
    ideal = sum(1.0 / math.log2(rank + 1) for rank in range(1, min(k, len(gold_set)) + 1))
    return dcg / ideal if ideal else 0.0


def compute_metrics(ranking: list[str], gold: Iterable[str]) -> dict[str, float]:
    """Compute Recall@5, Recall@10, MRR@10 and nDCG@10 for a ranking."""
    return {
        "recall@5": recall_at_k(ranking, gold, 5),
        "recall@10": recall_at_k(ranking, gold, 10),
        "mrr@10": mrr_at_k(ranking, gold, 10),
        "ndcg@10": ndcg_at_k(ranking, gold, 10),
    }
