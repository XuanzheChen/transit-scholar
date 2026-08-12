"""TransitScholar metadata extraction service."""

from transit_scholar.metadata.normalizers import (
    normalize_arxiv_id,
    normalize_author_name,
    normalize_doi,
    normalize_title,
)
from transit_scholar.metadata.result import MetadataExtractionResult
from transit_scholar.metadata.selection import (
    AUTHORS_FIELD,
    MANUAL_SOURCE_LOCATION,
    MANUAL_SOURCE_TYPE,
    PROVIDER_SOURCE_TYPE,
    NoPrimaryFileError,
    get_primary_file,
    materialize_selection,
    persist_manual_candidates,
    persist_provider_candidates,
    replace_paper_authors,
    reselect_and_materialize,
    select_candidates,
    source_tier,
)
from transit_scholar.metadata.service import extract_metadata_candidates

__all__ = [
    "extract_metadata_candidates",
    "MetadataExtractionResult",
    "normalize_title",
    "normalize_doi",
    "normalize_arxiv_id",
    "normalize_author_name",
    # Provenance / deterministic selection API.
    "AUTHORS_FIELD",
    "MANUAL_SOURCE_TYPE",
    "MANUAL_SOURCE_LOCATION",
    "PROVIDER_SOURCE_TYPE",
    "NoPrimaryFileError",
    "get_primary_file",
    "persist_provider_candidates",
    "persist_manual_candidates",
    "select_candidates",
    "materialize_selection",
    "reselect_and_materialize",
    "replace_paper_authors",
    "source_tier",
]
