"""Product composition services."""

from .conversation import ConversationGoalResolver, ConversationService
from .roles import BuiltinRoleActionPlanner, StructuredLLMRolePolicy
from .runtime import FileRunControlStore, FileRunResearchStateStore, RunScope, RuntimeFactory
from .research import PreparedMessage, ProductRunState, ResearchService
from .projection import ProductStateProjector
from .facade import PaperInUseError, RegisteredPaperFile, TransitScholarProduct
from .bootstrap import build_local_product

__all__ = [
    "BuiltinRoleActionPlanner",
    "ConversationGoalResolver",
    "ConversationService",
    "FileRunResearchStateStore",
    "FileRunControlStore",
    "RunScope",
    "RuntimeFactory",
    "StructuredLLMRolePolicy",
    "PreparedMessage", "ProductRunState", "ResearchService", "ProductStateProjector",
    "TransitScholarProduct", "PaperInUseError", "RegisteredPaperFile", "build_local_product",
]
