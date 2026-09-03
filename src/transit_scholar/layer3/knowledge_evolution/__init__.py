from .models import KnowledgeCandidate, AgenticWikiEntry, CandidateStatus

__all__ = ["KnowledgeCandidate", "AgenticWikiEntry", "CandidateStatus", "KnowledgePromotionService", "PromotionInput", "KnowledgePromotionRole"]

def __getattr__(name):
    if name in {"KnowledgePromotionService", "PromotionInput"}:
        from .service import KnowledgePromotionService, PromotionInput
        return {"KnowledgePromotionService": KnowledgePromotionService, "PromotionInput": PromotionInput}[name]
    if name == "KnowledgePromotionRole":
        from .roles import KnowledgePromotionRole
        return KnowledgePromotionRole
    raise AttributeError(name)
