"""TransitScholar ingestion service."""

from transit_scholar.ingestion.result import ImportResult
from transit_scholar.ingestion.service import import_paper

__all__ = ["import_paper", "ImportResult"]
