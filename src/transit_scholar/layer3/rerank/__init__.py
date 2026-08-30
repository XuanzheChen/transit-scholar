"""Provider-neutral cross-Paper reranking components."""

from .model import (
    CrossPaperRanker,
    DedicatedModelReranker,
    ModelThenFineRanker,
    RerankDiagnostics,
)
from .llm import EvidenceRankingProvider, LLMFineRerankDiagnostics, LLMFineReranker
from .scheduler import (
    EliminationRound,
    LLMEliminationSchedule,
    LLMFineRerankConfig,
    allocate_group_quotas,
    build_elimination_schedule,
    recompute_round_quota,
    regroup_candidates,
    validate_round_quotas,
)

__all__ = [
    "CrossPaperRanker",
    "DedicatedModelReranker",
    "ModelThenFineRanker",
    "RerankDiagnostics",
    "EvidenceRankingProvider",
    "LLMFineRerankDiagnostics",
    "LLMFineReranker",
    "EliminationRound",
    "LLMEliminationSchedule",
    "LLMFineRerankConfig",
    "allocate_group_quotas",
    "build_elimination_schedule",
    "recompute_round_quota",
    "regroup_candidates",
    "validate_round_quotas",
]
