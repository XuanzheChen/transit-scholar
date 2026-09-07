"""Product composition services."""

from .conversation import ConversationGoalResolver, ConversationService
from .roles import BuiltinRoleActionPlanner, StructuredLLMRolePolicy
from .runtime import FileRunResearchStateStore, RunScope, RuntimeFactory
from .research import ProductRunState, ResearchService
from .projection import ProductStateProjector
from .facade import TransitScholarProduct
from .bootstrap import build_local_product

__all__ = [
    "BuiltinRoleActionPlanner",
    "ConversationGoalResolver",
    "ConversationService",
    "FileRunResearchStateStore",
    "RunScope",
    "RuntimeFactory",
    "StructuredLLMRolePolicy",
    "ProductRunState", "ResearchService", "ProductStateProjector",
    "TransitScholarProduct", "build_local_product",
]
