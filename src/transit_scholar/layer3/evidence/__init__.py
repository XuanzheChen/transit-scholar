"""Framework-neutral evidence provenance APIs for Layer3."""

from .models import EvidenceLocator, EvidenceSpan
from .research import PaperProvenance, QueryProvenance, ResearchEvidence

__all__ = [
    "EvidenceLocator",
    "EvidenceSpan",
    "PaperProvenance",
    "QueryProvenance",
    "ResearchEvidence",
]
