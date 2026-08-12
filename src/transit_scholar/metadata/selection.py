"""Deterministic metadata candidate persistence, selection and materialization.

Source tiers (frozen, highest priority first):

    manual_confirmed > doi_provider > heuristic

``manual_confirmed`` candidates are written by ``identity.service`` when the
user confirms a field (source_location=user_edit). ``doi_provider`` candidates
are written by the DOI enrichment service with ``source_location`` equal to the
exact provider key (crossref / openalex / semantic_scholar). Every other source
type (pdf_metadata, first_pages_text, filename_parser, pdf_reader, ...) is a
heuristic source.

Rules implemented here:

- Every usable provider scalar is persisted as a MetadataCandidate attached to
  the paper's non-deleted primary file. Author lists are persisted as ONE
  aggregate candidate (field_name=authors) whose value_text is a canonical
  JSON array of non-empty author-name strings in provider order.
- Candidates are deduplicated by (paper, file, field, logical normalized value,
  source_type, source_location), so repeated refreshes create no noise.
- Confidence is NOT an eligibility threshold. Within a tier it is only a
  deterministic tie-breaker, applied after the frozen per-field provider
  priority (FIELD_PRIORITY in doi_enrichment.merger), then by newest
  created_at and id for stability.
- At most one candidate per logical field is selected. Title, DOI, arXiv ID,
  year, venue, abstract and the aggregate authors list are materialized into
  Paper/PaperAuthor. Title always goes through normalize_title(). Aggregate
  authors replace PaperAuthor rows atomically with author_order 1..N; if no
  aggregate authors candidate exists, existing rows are never erased.
- Legacy field_name=author heuristic candidates stay readable history and are
  never migrated or reinterpreted.
- DOI is normalized with the same normalize_doi() on both sides before any
  comparison; a provider whose DOI mismatches contributes zero candidates (the
  caller records the persisted mismatch fact).
- Publisher may be stored as a candidate but is never materialized: Paper has
  no publisher column by design.

Every selected candidate materializes into Paper/PaperAuthor, including DOI
and arXiv ID (normalized with normalize_doi / normalize_arxiv_id), so the
materialized state can never diverge from the selected candidate state.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Iterable

from sqlalchemy import delete, select

# NOTE: doi_enrichment modules are intentionally NOT imported at module level.
# Importing any transit_scholar.doi_enrichment submodule runs that package's
# __init__, which imports doi_enrichment.service, which imports this module --
# a cycle when transit_scholar.metadata is still initializing. Field/providers
# dependencies are therefore imported lazily inside the functions that use them.
from transit_scholar.db.models import MetadataCandidate, Paper, PaperAuthor, PaperFile
from transit_scholar.metadata.normalizers import (
    is_plausible_publication_year,
    normalize_arxiv_id,
    normalize_author_name,
    normalize_doi,
    normalize_title,
)

# --- Frozen source vocabulary -------------------------------------------------
MANUAL_SOURCE_TYPE = "manual_confirmed"
MANUAL_SOURCE_LOCATION = "user_edit"
PROVIDER_SOURCE_TYPE = "doi_provider"

# Aggregate authors candidate field name (canonical JSON array of names).
AUTHORS_FIELD = "authors"

# Logical scalar fields that participate in selection.
SCALAR_FIELDS = ("title", "doi", "arxiv_id", "publication_year", "venue", "abstract")
# Every field that participates in selection, including the aggregate authors
# list and the never-materialized publisher fact.
LOGICAL_FIELDS = SCALAR_FIELDS + (AUTHORS_FIELD, "publisher")

_EPOCH = datetime(1970, 1, 1)
_WS_RE = re.compile(r"\s+")


class NoPrimaryFileError(RuntimeError):
    """Raised when candidate persistence needs a non-deleted primary file.

    Candidate rows require a paper_file_id; we never fabricate a file or
    schema value, so callers must resolve the paper's primary file first.
    """


def source_tier(source_type: str) -> int:
    """Return the tier rank of a candidate source (lower wins)."""
    if source_type == MANUAL_SOURCE_TYPE:
        return 0
    if source_type == PROVIDER_SOURCE_TYPE:
        return 1
    return 2


def get_primary_file(session, paper_id: str) -> PaperFile:
    """Return the paper's existing non-deleted primary file.

    Raises ``NoPrimaryFileError`` when the paper has none, instead of
    fabricating a file reference.
    """
    primary = session.execute(
        select(PaperFile).where(
            PaperFile.paper_id == paper_id,
            PaperFile.is_primary.is_(True),
            PaperFile.deleted_at.is_(None),
        )
    ).scalars().first()
    if primary is None:
        raise NoPrimaryFileError(
            f"Paper {paper_id} has no non-deleted primary file to attach candidates to"
        )
    return primary


def persist_provider_candidates(
    session,
    paper: Paper,
    paper_file: PaperFile,
    parsed: dict[str, ParsedFields],
    *,
    skip_providers: Iterable[str] = (),
) -> int:
    """Persist every usable provider scalar/aggregate as a candidate.

    ``source_type`` is ``doi_provider`` and ``source_location`` is the exact
    provider key. Providers listed in ``skip_providers`` (DOI mismatch) are
    skipped entirely. Repeated refreshes with the same logical values create
    no new rows. Returns the number of candidates created.
    """
    created = 0
    for provider, fields in parsed.items():
        if provider in skip_providers:
            continue
        for field_name, value_text in _provider_field_values(fields).items():
            if _candidate_exists(
                session, paper.id, paper_file.id, field_name, value_text,
                PROVIDER_SOURCE_TYPE, provider,
            ):
                continue
            session.add(MetadataCandidate(
                paper_id=paper.id,
                paper_file_id=paper_file.id,
                field_name=field_name,
                value_text=value_text,
                source_type=PROVIDER_SOURCE_TYPE,
                source_location=provider,
                confidence=1.0,
            ))
            created += 1
    return created


def persist_manual_candidates(
    session,
    paper: Paper,
    paper_file: PaperFile,
    fields: dict[str, Any],
) -> int:
    """Persist user-confirmed field values as manual_confirmed candidates.

    Authors are stored as the aggregate canonical JSON array. Empty values are
    skipped. Returns the number of candidates created.
    """
    created = 0
    for field_name, value in fields.items():
        value_text = _manual_value_text(field_name, value)
        if value_text is None:
            continue
        if _candidate_exists(
            session, paper.id, paper_file.id, field_name, value_text,
            MANUAL_SOURCE_TYPE, MANUAL_SOURCE_LOCATION,
        ):
            continue
        session.add(MetadataCandidate(
            paper_id=paper.id,
            paper_file_id=paper_file.id,
            field_name=field_name,
            value_text=value_text,
            source_type=MANUAL_SOURCE_TYPE,
            source_location=MANUAL_SOURCE_LOCATION,
            confidence=1.0,
        ))
        created += 1
    return created


def select_candidates(session, paper_id: str) -> dict[str, MetadataCandidate]:
    """Recompute the deterministic selection for every logical field.

    Marks exactly one candidate per logical field as selected (other
    candidates of that field are unselected). Legacy fields such as ``author``
    and file-fact fields are left untouched as readable history. Returns a
    mapping field_name -> selected MetadataCandidate for fields that have at
    least one candidate.
    """
    rows = session.execute(
        select(MetadataCandidate)
        .where(MetadataCandidate.paper_id == paper_id)
        .order_by(MetadataCandidate.created_at, MetadataCandidate.id)
    ).scalars().all()

    selected: dict[str, MetadataCandidate] = {}
    for field_name in LOGICAL_FIELDS:
        group = [c for c in rows if c.field_name == field_name]
        if not group:
            continue
        chosen = _best_candidate(field_name, group)
        for candidate in group:
            candidate.is_selected = candidate is chosen
        selected[field_name] = chosen
    return selected


def materialize_selection(
    session,
    paper: Paper,
    selected: dict[str, MetadataCandidate],
) -> list[str]:
    """Materialize the selected candidates into Paper/PaperAuthor.

    Returns the list of fields actually written. Every selected candidate
    materializes unconditionally, including DOI and arXiv ID (normalized with
    normalize_doi / normalize_arxiv_id), so the materialized Paper state can
    never diverge from the selected candidate state. Title always goes through
    normalize_title(). Aggregate authors replace PaperAuthor rows atomically
    with author_order 1..N (identical lists are left untouched). Publisher is
    never materialized: Paper has no publisher column.
    """
    materialized: list[str] = []

    title_c = selected.get("title")
    if title_c is not None:
        paper.title = title_c.value_text.strip()
        paper.normalized_title = normalize_title(title_c.value_text)
        materialized.append("title")

    doi_c = selected.get("doi")
    if doi_c is not None:
        paper.doi = doi_c.value_text.strip()
        paper.normalized_doi = normalize_doi(doi_c.value_text)
        materialized.append("doi")

    arxiv_c = selected.get("arxiv_id")
    if arxiv_c is not None:
        paper.arxiv_id = normalize_arxiv_id(arxiv_c.value_text)
        materialized.append("arxiv_id")

    year_c = selected.get("publication_year")
    if year_c is not None:
        try:
            year_value = int(year_c.value_text.strip())
        except (TypeError, ValueError):
            pass
        else:
            # Structured/provider year paths use the same plausible-range
            # validator as heuristic paths before materialization.
            if is_plausible_publication_year(year_value):
                paper.publication_year = year_value
                materialized.append("publication_year")

    venue_c = selected.get("venue")
    if venue_c is not None:
        paper.venue = venue_c.value_text.strip()
        materialized.append("venue")

    abstract_c = selected.get("abstract")
    if abstract_c is not None:
        paper.abstract = abstract_c.value_text.strip()
        materialized.append("abstract")

    authors_c = selected.get(AUTHORS_FIELD)
    if authors_c is not None:
        names = _decode_author_names(authors_c.value_text)
        if names:
            replace_paper_authors(session, paper, names)
            materialized.append(AUTHORS_FIELD)

    return materialized


def reselect_and_materialize(session, paper: Paper) -> dict[str, str]:
    """Recompute selection from all persisted candidates and materialize.

    Returns a mapping of materialized field -> source label (for example
    ``doi_provider:crossref`` or ``manual_confirmed:user_edit``).
    """
    selected = select_candidates(session, paper.id)
    materialized = materialize_selection(session, paper, selected)
    return {field: _source_label(selected[field]) for field in materialized}


def replace_paper_authors(session, paper: Paper, names: list[str]) -> bool:
    """Atomically replace the paper's PaperAuthor rows with ``names``.

    author_order starts at 1. When the current ordered names already equal
    ``names`` the rows are left untouched (idempotency). Returns True when
    rows were replaced.
    """
    existing = session.execute(
        select(PaperAuthor)
        .where(PaperAuthor.paper_id == paper.id)
        .order_by(PaperAuthor.author_order)
    ).scalars().all()
    if [a.full_name for a in existing] == names:
        return False
    session.execute(delete(PaperAuthor).where(PaperAuthor.paper_id == paper.id))
    for order, name in enumerate(names, start=1):
        session.add(PaperAuthor(
            paper_id=paper.id,
            author_order=order,
            full_name=name,
            normalized_name=normalize_author_name(name),
        ))
    return True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _provider_field_values(fields: ParsedFields) -> dict[str, str]:
    """Map parsed provider fields to candidate (field_name, value_text)."""
    values: dict[str, str] = {}
    if fields.title:
        values["title"] = fields.title.strip()
    if fields.doi:
        values["doi"] = fields.doi.strip()
    if fields.arxiv_id:
        values["arxiv_id"] = fields.arxiv_id.strip()
    if fields.publication_year is not None:
        values["publication_year"] = str(fields.publication_year)
    if fields.venue:
        values["venue"] = fields.venue.strip()
    if fields.publisher:
        values["publisher"] = fields.publisher.strip()
    if fields.abstract:
        values["abstract"] = fields.abstract.strip()
    names = [a.full_name.strip() for a in fields.authors if a.full_name and a.full_name.strip()]
    if names:
        values[AUTHORS_FIELD] = json.dumps(names, ensure_ascii=False)
    return values


def _manual_value_text(field_name: str, value: Any) -> str | None:
    """Encode one user-supplied field value as candidate value_text."""
    if field_name == AUTHORS_FIELD:
        names = [str(n).strip() for n in value if str(n).strip()]
        return json.dumps(names, ensure_ascii=False) if names else None
    text = str(value).strip() if value is not None else ""
    return text or None


def _candidate_exists(
    session,
    paper_id: str,
    file_id: str,
    field_name: str,
    value_text: str,
    source_type: str,
    source_location: str,
) -> bool:
    """True when a candidate with the same logical normalized value exists."""
    canonical = _canonical_value(field_name, value_text)
    for row in session.execute(
        select(MetadataCandidate).where(
            MetadataCandidate.paper_id == paper_id,
            MetadataCandidate.paper_file_id == file_id,
            MetadataCandidate.field_name == field_name,
            MetadataCandidate.source_type == source_type,
            MetadataCandidate.source_location == source_location,
        )
    ).scalars().all():
        if _canonical_value(row.field_name, row.value_text) == canonical:
            return True
    return False


def _canonical_value(field_name: str, value_text: str) -> str:
    """Logical normalized value used for deduplication."""
    if field_name == "title":
        return normalize_title(value_text) or ""
    if field_name == "doi":
        return normalize_doi(value_text) or ""
    if field_name == "arxiv_id":
        return normalize_arxiv_id(value_text) or ""
    if field_name == "publication_year":
        try:
            return str(int(value_text.strip()))
        except (TypeError, ValueError):
            return value_text.strip()
    if field_name == AUTHORS_FIELD:
        names = _decode_author_names(value_text)
        return json.dumps(
            [normalize_author_name(n) or "" for n in names], ensure_ascii=False
        )
    return _WS_RE.sub(" ", value_text.strip()).lower()


def _best_candidate(
    field_name: str, group: list[MetadataCandidate]
) -> MetadataCandidate:
    """Pick the deterministic winner within one field's candidates."""
    manual = [c for c in group if c.source_type == MANUAL_SOURCE_TYPE]
    if manual:
        return _pick(manual)
    providers = [c for c in group if c.source_type == PROVIDER_SOURCE_TYPE]
    if providers:
        best_rank = min(_provider_rank(field_name, c.source_location) for c in providers)
        return _pick([
            c for c in providers
            if _provider_rank(field_name, c.source_location) == best_rank
        ])
    return _pick(group)


def _provider_rank(field_name: str, source_location: str) -> int:
    """Frozen per-field provider priority (best provider ranks lowest)."""
    # Lazy import: see the module-level NOTE about import cycles.
    from transit_scholar.doi_enrichment.merger import FIELD_PRIORITY

    priority = FIELD_PRIORITY.get(field_name, ())
    if source_location in priority:
        return priority.index(source_location)
    return len(priority)


def _pick(candidates: list[MetadataCandidate]) -> MetadataCandidate:
    """Deterministic tie-breaker: confidence desc, then created_at desc, then id.

    Newest-first keeps the latest user edit winning among equal-confidence
    manual candidates; the id tie-break keeps the result stable for rows
    created in the same instant. ``datetime`` does not support unary minus, so
    the descending created_at order is expressed as ``_EPOCH - created_at``
    (a timedelta, which compares in the right direction). A missing
    created_at falls back to the epoch.
    """

    def key(candidate: MetadataCandidate):
        created = candidate.created_at or _EPOCH
        return (-candidate.confidence, _EPOCH - created, candidate.id)

    return min(candidates, key=key)


def _decode_author_names(value_text: str) -> list[str]:
    """Decode the canonical JSON array of author names (empty-safe)."""
    try:
        names = json.loads(value_text)
    except (TypeError, ValueError):
        return []
    if not isinstance(names, list):
        return []
    return [str(n).strip() for n in names if str(n).strip()]


def _source_label(candidate: MetadataCandidate) -> str:
    return f"{candidate.source_type}:{candidate.source_location}"
