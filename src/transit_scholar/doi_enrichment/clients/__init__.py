"""Provider client sub-package."""

from transit_scholar.doi_enrichment.clients.base import ProviderClient
from transit_scholar.doi_enrichment.clients.crossref import CrossrefClient
from transit_scholar.doi_enrichment.clients.openalex import OpenAlexClient
from transit_scholar.doi_enrichment.clients.semantic_scholar import SemanticScholarClient

__all__ = [
    "CrossrefClient",
    "OpenAlexClient",
    "ProviderClient",
    "SemanticScholarClient",
]
