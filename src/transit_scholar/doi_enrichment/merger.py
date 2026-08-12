"""Parse provider raw JSON and merge fields into a paper by priority.

Field priority follows Phase 1 spec §17: Crossref > OpenAlex > Semantic
Scholar for most fields, with abstract preferring Semantic Scholar. A
provider whose returned DOI does not match the paper's normalized DOI
contributes no fields. Manual-confirmed fields are never overwritten.
"""

from __future__ import annotations

import json
import re
from typing import Any

from transit_scholar.doi_enrichment.result import ParsedAuthor, ParsedFields
from transit_scholar.metadata.normalizers import normalize_doi

# Per-field provider preference (best -> worst). See Phase 1 spec §17.2.
FIELD_PRIORITY: dict[str, tuple[str, ...]] = {
    "title": ("crossref", "openalex", "semantic_scholar"),
    "authors": ("crossref", "openalex", "semantic_scholar"),
    "publication_year": ("crossref", "openalex", "semantic_scholar"),
    "venue": ("crossref", "openalex", "semantic_scholar"),
    "publisher": ("crossref", "openalex"),
    "abstract": ("semantic_scholar", "crossref", "openalex"),
    "doi": ("crossref", "openalex", "semantic_scholar"),
    "arxiv_id": ("openalex", "semantic_scholar"),
}

# Minimum field set for a job to be considered ``fetched`` (Phase 1 §17.3).
MINIMUM_FIELDS = ("doi", "title", "authors", "publication_year")


def parse_crossref(raw_json: str) -> ParsedFields:
    """Parse a Crossref ``message`` object into unified fields."""
    try:
        data = json.loads(raw_json)
    except (ValueError, TypeError):
        return ParsedFields(source_location="crossref")
    message = data.get("message") if isinstance(data, dict) else data
    if not isinstance(message, dict):
        return ParsedFields(source_location="crossref")

    title_list = message.get("title") or []
    title = title_list[0] if title_list else None

    authors = []
    for a in message.get("author") or []:
        given = a.get("given", "")
        family = a.get("family", "")
        name = " ".join(p for p in (given, family) if p).strip()
        if name:
            authors.append(ParsedAuthor(
                full_name=name,
                orcid=a.get("ORCID"),
                affiliation=(a.get("affiliation") or [{}])[0].get("name"),
            ))

    year = None
    for key in ("published-print", "published-online", "issued", "created"):
        container = message.get(key)
        if isinstance(container, dict):
            parts = container.get("date-parts")
            if parts and parts[0] and parts[0][0]:
                try:
                    year = int(parts[0][0])
                except (TypeError, ValueError):
                    year = None
                if year is not None:
                    break

    venue_list = message.get("container-title") or []
    venue = venue_list[0] if venue_list else None
    publisher = message.get("publisher")
    doi = message.get("DOI")
    abstract = _clean_jats(message.get("abstract"))
    arxiv = _first_external_id(message, "arxiv")
    return ParsedFields(
        title=title,
        authors=authors,
        publication_year=year,
        venue=venue,
        publisher=publisher,
        abstract=abstract,
        doi=doi,
        arxiv_id=arxiv,
        source_location="crossref",
    )


def parse_openalex(raw_json: str) -> ParsedFields:
    """Parse an OpenAlex work object into unified fields."""
    try:
        work = json.loads(raw_json)
    except (ValueError, TypeError):
        return ParsedFields(source_location="openalex")
    if not isinstance(work, dict):
        return ParsedFields(source_location="openalex")

    title = work.get("title") or work.get("display_name")

    authors = []
    for a in work.get("authorships") or []:
        author = a.get("author") if isinstance(a, dict) else None
        if not isinstance(author, dict):
            continue
        name = author.get("display_name")
        if not name:
            continue
        affiliations = [
            aff.get("display_name")
            for aff in (a.get("affiliations") or [])
            if isinstance(aff, dict)
        ]
        authors.append(ParsedAuthor(
            full_name=name,
            orcid=author.get("orcid"),
            affiliation=affiliations[0] if affiliations else None,
        ))

    year = work.get("publication_year")
    venue = _openalex_venue(work)
    publisher = None
    doi = work.get("doi")
    abstract = work.get("abstract_inverted_index") and _reconstruct_abstract(
        work["abstract_inverted_index"]
    )
    arxiv = _first_external_id(work, "arxiv")
    return ParsedFields(
        title=title,
        authors=authors,
        publication_year=year,
        venue=venue,
        publisher=publisher,
        abstract=abstract,
        doi=doi,
        arxiv_id=arxiv,
        source_location="openalex",
    )


def parse_semantic_scholar(raw_json: str) -> ParsedFields:
    """Parse a Semantic Scholar paper object into unified fields."""
    try:
        paper = json.loads(raw_json)
    except (ValueError, TypeError):
        return ParsedFields(source_location="semantic_scholar")
    if not isinstance(paper, dict):
        return ParsedFields(source_location="semantic_scholar")

    title = paper.get("title")

    authors = []
    for a in paper.get("authors") or []:
        if isinstance(a, dict) and a.get("name"):
            authors.append(ParsedAuthor(full_name=a["name"]))

    year = paper.get("year")
    venue = paper.get("venue") or paper.get("journal")
    publisher = None
    doi = paper.get("doi") or paper.get("externalIds", {}).get("DOI")
    abstract = paper.get("abstract")
    arxiv = paper.get("externalIds", {}).get("ArXiv")
    return ParsedFields(
        title=title,
        authors=authors,
        publication_year=year,
        venue=venue,
        publisher=publisher,
        abstract=abstract,
        doi=doi,
        arxiv_id=arxiv,
        source_location="semantic_scholar",
    )


def _openalex_venue(work: dict) -> str | None:
    for loc in (work.get("primary_location"), work.get("best_oa_location")):
        if isinstance(loc, dict):
            source = loc.get("source")
            if isinstance(source, dict) and source.get("display_name"):
                return source["display_name"]
    for loc in work.get("locations") or []:
        if isinstance(loc, dict):
            source = loc.get("source")
            if isinstance(source, dict) and source.get("display_name"):
                return source["display_name"]
    return None


def _first_external_id(record: dict, key: str) -> str | None:
    for entry in record.get("external_ids") or []:
        if isinstance(entry, dict) and entry.get("type") == key:
            return entry.get("value")
    return None


_JATS_RE = re.compile(r"<[^>]+>")


def _clean_jats(text: str | None) -> str | None:
    if not text:
        return None
    cleaned = _JATS_RE.sub("", text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or None


def _reconstruct_abstract(index: Any) -> str | None:
    if not isinstance(index, dict):
        return None
    pairs = []
    for word, positions in index.items():
        for pos in positions:
            pairs.append((pos, word))
    pairs.sort(key=lambda p: p[0])
    return " ".join(w for _, w in pairs) or None


def doi_consistent(candidate_doi: str | None, normalized_doi: str | None) -> bool:
    """True when a provider DOI is usable against the paper's local DOI.

    Both sides are normalized with the same ``normalize_doi`` before the
    comparison, so bare, ``doi:``-prefixed, ``https://doi.org/`` and case/
    whitespace variants compare identically. A provider that returned no DOI
    is consistent (its other fields may still be used); a paper without a
    local DOI cannot mismatch.
    """
    if not candidate_doi:
        return True
    provider_doi = normalize_doi(candidate_doi)
    if not provider_doi:
        return True
    if not normalized_doi:
        return True
    return provider_doi == normalize_doi(normalized_doi)


def best_field(
    field: str,
    parsed: dict[str, ParsedFields],
    normalized_doi: str | None,
    manual_confirmed: set[str],
) -> tuple[str, Any] | None:
    """Return ``(source, value)`` for a field using the priority rules.

    Returns ``None`` when the field is manual-confirmed or no eligible
    provider supplies it. Providers whose DOI mismatches are skipped.
    """
    if field in manual_confirmed:
        return None
    for provider in FIELD_PRIORITY.get(field, ()):
        candidate = parsed.get(provider)
        if candidate is None:
            continue
        if not doi_consistent(candidate.doi, normalized_doi):
            continue
        value = getattr(candidate, field, None)
        if field == "authors":
            if value:
                return provider, value
        elif value not in (None, ""):
            return provider, value
    return None


def minimum_fields_satisfied(resolved: dict[str, str]) -> bool:
    """True when the minimum field set is present in the resolved map."""
    if not resolved.get("doi") or not resolved.get("title"):
        return False
    if "authors" not in resolved:
        return False
    if not resolved.get("publication_year"):
        return False
    return True
