"""DOI metadata enrichment package.

Fills ``title``/``authors``/``publication_year``/``venue``/``abstract`` for a
paper by querying Crossref, OpenAlex and Semantic Scholar in cascade order,
caching each provider's raw JSON locally and merging fields by a fixed
priority. Network access is gated by ``settings.metadata_enrichment_allow_network``
and all provider traffic is expected to be mocked in tests.
"""

from transit_scholar.doi_enrichment.result import (
    EnrichmentJobResult,
    ParsedAuthor,
    ParsedFields,
    ProviderFetchResult,
    ProviderResult,
    ProviderStatus,
)
from transit_scholar.doi_enrichment.service import (
    derive_enrichment_status,
    enrich_paper_by_doi,
    refresh_enrichment,
)

__all__ = [
    "EnrichmentJobResult",
    "ParsedAuthor",
    "ParsedFields",
    "ProviderFetchResult",
    "ProviderResult",
    "ProviderStatus",
    "derive_enrichment_status",
    "enrich_paper_by_doi",
    "refresh_enrichment",
]
