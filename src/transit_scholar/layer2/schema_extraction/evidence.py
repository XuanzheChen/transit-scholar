"""Candidate evidence mapping and evidence binding (FR-B-005/006).

Retrieval hits are mapped to stable candidate ids ``E1``, ``E2``, ... in
retrieval rank order, then LLM-selected ``evidence_ids`` are rebound into
Package A ``EvidenceRef`` objects purely by program logic. Provenance fields
(``block_id``, ``char_start``, ``char_end``, ``pages``, ``section_path``,
``quote``) always come from the L2S1 ``RetrievalHit`` / ``SourceRef`` (plus the
canonical block layer) and never from the LLM payload.

Per-ref coherence rule (FR-003 / AC-T001-F10): each ``EvidenceRef`` is built
from a single canonical block / single source ref atom. The ``quote`` is the
deterministic substring ``block_text[char_start:char_end]`` and ``pages`` /
``section_path`` come from that same block's (or that ref's) provenance.
Candidate-wide ``text`` / ``pages`` / ``section_path`` are only reused when the
candidate has a single source ref (so there is nothing to "share across refs")
and the caller explicitly allows the offline candidate fallback.

``enrich_candidates_with_blocks`` fills each ``SourceRefRecord`` with per-ref
canonical ``text`` / ``pages`` / ``section_path`` from canonical block data;
``bind_evidence`` then never fabricates coordinates, quotes, or pages.

Failure policy (never silent, never fabricated): unknown ids raise
``UnknownEvidenceIdError``; a selected candidate without source refs, or a
ref whose per-ref provenance cannot be obtained (and no legal single-ref
fallback is allowed), raises ``EvidenceBindingError`` and produces no
``EvidenceRef``.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator

from .errors import EvidenceBindingError, UnknownEvidenceIdError
from .models import EvidenceRef

#: L2S1 SourceRef is a plain dataclass; this pydantic mirror keeps candidates
#: JSON-serializable without importing the L2S1 schema module at runtime.
#: The optional per-ref ``text`` / ``pages`` / ``section_path`` hold provenance
#: for the *single canonical block* this ref points at (filled by
#: ``enrich_candidates_with_blocks`` or by richer L2S1 source-ref data).
class SourceRefRecord(BaseModel):
    """JSON-safe mirror of ``transit_scholar.layer2.schema.SourceRef``."""

    block_id: str = Field(min_length=1)
    char_start: int = Field(default=0, ge=0)
    char_end: int = Field(default=0, ge=0)
    text: str | None = None
    pages: list[int] | None = None
    section_path: list[str] | None = None

    @model_validator(mode="after")
    def _check_char_range(self) -> "SourceRefRecord":
        if self.char_end < self.char_start:
            raise ValueError(
                f"char_end ({self.char_end}) must be >= char_start ({self.char_start})"
            )
        return self


class CandidateEvidence(BaseModel):
    """One numbered retrieval candidate (FR-B-005). All fields derive from
    ``RetrievalHit`` / ``SourceRef``; none comes from the LLM."""

    evidence_id: str
    rank: int
    method: str
    score: float | None = None
    chunk_id: str | None = None
    paper_id: str = ""
    source_refs: list[SourceRefRecord] = Field(default_factory=list)
    pages: list[int] = Field(default_factory=list)
    section_path: list[str] = Field(default_factory=list)
    text: str = ""


def _to_source_ref_record(ref: Any) -> SourceRefRecord:
    if isinstance(ref, SourceRefRecord):
        return ref
    per_ref = {}
    to_dict = getattr(ref, "to_dict", None)
    if callable(to_dict):
        data = to_dict()
        if isinstance(data, dict):
            per_ref = {
                key: data.get(key)
                for key in ("text", "pages", "section_path")
                if data.get(key) is not None
            }
    else:
        for key in ("text", "pages", "section_path"):
            value = getattr(ref, key, None)
            if value is not None:
                per_ref[key] = value
    return SourceRefRecord(
        block_id=str(ref.block_id),
        char_start=int(ref.char_start),
        char_end=int(ref.char_end),
        **per_ref,
    )


def _hit_attr(hit: Any, name: str, default: Any) -> Any:
    value = getattr(hit, name, default)
    return value if value is not None else default


def map_hits_to_candidates(hits: list[Any]) -> list[CandidateEvidence]:
    """Map retrieval hits to stable candidate ids ``E1..En``.

    Candidates are numbered in ascending ``rank`` order; hits with equal rank
    keep their input order (stable sort). Identical input always yields
    identical mapping. Per-ref provenance is copied from the hit's source
    refs when the hit carries it (richer L2S1 source-ref data); otherwise the
    per-ref fields stay ``None`` until ``enrich_candidates_with_blocks`` is
    called by the engine.
    """
    ordered = sorted(hits, key=lambda hit: hit.rank)
    candidates: list[CandidateEvidence] = []
    for index, hit in enumerate(ordered):
        candidates.append(
            CandidateEvidence(
                evidence_id=f"E{index + 1}",
                rank=hit.rank,
                method=_hit_attr(hit, "retrieval_method", "") or "",
                score=_hit_attr(hit, "score", None),
                chunk_id=_hit_attr(hit, "chunk_id", None),
                paper_id=_hit_attr(hit, "paper_id", "") or "",
                source_refs=[
                    _to_source_ref_record(ref)
                    for ref in (_hit_attr(hit, "source_refs", []) or [])
                ],
                pages=list(_hit_attr(hit, "pages", []) or []),
                section_path=list(_hit_attr(hit, "section_path", []) or []),
                text=_hit_attr(hit, "text", "") or "",
            )
        )
    return candidates


def _dedupe_source_refs(refs: list[SourceRefRecord]) -> list[SourceRefRecord]:
    seen: set[tuple[str, int, int]] = set()
    unique: list[SourceRefRecord] = []
    for ref in refs:
        key = (ref.block_id, ref.char_start, ref.char_end)
        if key in seen:
            continue
        seen.add(key)
        unique.append(ref)
    return unique


def _block_pages(block: dict[str, Any]) -> list[int]:
    """Deterministic per-block page list from ``pages`` + ``provenance``."""
    pages: list[int] = []
    raw_pages = block.get("pages")
    if isinstance(raw_pages, list):
        for page in raw_pages:
            if isinstance(page, int):
                pages.append(page)
    provenance = block.get("provenance")
    if isinstance(provenance, list):
        for item in provenance:
            if isinstance(item, dict) and item.get("page") is not None:
                try:
                    pages.append(int(item["page"]))
                except (TypeError, ValueError):
                    pass
    return sorted(set(pages))


def _block_section_path(block: dict[str, Any]) -> list[str] | None:
    value = block.get("section_path")
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return list(value)
    content = block.get("content")
    if isinstance(content, dict):
        value = content.get("section_path")
        if isinstance(value, list) and all(isinstance(item, str) for item in value):
            return list(value)
    relations = block.get("relations")
    if isinstance(relations, dict):
        value = relations.get("section_path")
        if isinstance(value, list) and all(isinstance(item, str) for item in value):
            return list(value)
    return None


def enrich_candidates_with_blocks(
    candidates: list[CandidateEvidence],
    block_map: dict[str, dict[str, Any]] | None,
) -> None:
    """Fill each source ref's per-ref provenance from canonical block data.

    ``block_map`` maps ``block_id -> canonical block dict`` (the shape returned
    by the L2S1 canonical reader). Deterministic and side-effect-free with
    respect to coordinates: missing blocks / missing fields leave the per-ref
    provenance untouched (``None``) so ``bind_evidence`` can decide whether a
    legal fallback exists or the ref must be rejected (never fabricated).
    """
    if not block_map:
        return
    for candidate in candidates:
        for ref in candidate.source_refs:
            block = block_map.get(ref.block_id)
            if not isinstance(block, dict):
                continue
            text = block.get("text")
            if isinstance(text, str) and text:
                ref.text = text
            block_pages = _block_pages(block)
            if block_pages:
                ref.pages = block_pages
            else:
                ref.pages = []
            section_path = _block_section_path(block)
            if section_path is not None:
                ref.section_path = section_path
            else:
                ref.section_path = []


def bind_evidence(
    evidence_ids: list[str],
    candidates: list[CandidateEvidence],
    *,
    field_id: str | None = None,
    allow_candidate_fallback: bool = True,
    warnings_out: list[str] | None = None,
) -> list[EvidenceRef]:
    """Rebind LLM-selected ``evidence_ids`` into Package A ``EvidenceRef``.

    Every ``EvidenceRef`` is produced from one source-ref atom:

    - ``block_id`` / ``char_start`` / ``char_end`` come from the candidate's
      ``SourceRefRecord``;
    - ``quote = block_text[char_start:char_end]`` (deterministic substring)
      and ``pages`` / ``section_path`` come from that same source ref's
      per-ref provenance (typically filled by ``enrich_candidates_with_blocks``
      from canonical block data);
    - when a ref carries no per-ref provenance, the candidate-wide data is
      reused ONLY for a single-source-ref candidate AND when
      ``allow_candidate_fallback`` is true (offline deterministic default);
    - otherwise the ref is skipped with an explicit warning appended to
      ``warnings_out`` (AC-T001-F11: "skip that ref with an explicit manifest
      warning recorded") — never fabricated;
    - unknown ids raise ``UnknownEvidenceIdError`` (checked up front, no
      partial binding);
    - a selected candidate with no source refs raises
      ``EvidenceBindingError`` and yields no fabricated ``EvidenceRef``;
    - if no ``EvidenceRef`` could be bound from any selected evidence id
      (every ref was skipped), ``EvidenceBindingError`` is raised so the
      engine falls back to the ``unclear`` placeholder instead of returning an
      assertive value with zero evidence.
    """
    by_id = {candidate.evidence_id: candidate for candidate in candidates}
    unknown = [eid for eid in evidence_ids if eid not in by_id]
    if unknown:
        raise UnknownEvidenceIdError(
            unknown[0],
            field_id=field_id,
            known_ids=sorted(by_id),
        )
    refs: list[EvidenceRef] = []
    skipped: list[str] = []
    seen: set[str] = set()
    for eid in evidence_ids:
        if eid in seen:
            continue
        seen.add(eid)
        candidate = by_id[eid]
        if not candidate.source_refs:
            raise EvidenceBindingError(
                f"candidate {eid!r} for field {field_id!r} has no source refs "
                "to bind; refusing to fabricate EvidenceRef",
                field_id=field_id,
            )
        unique_refs = _dedupe_source_refs(candidate.source_refs)
        for ref in unique_refs:
            text: str | None = ref.text
            pages: list[int] | None = ref.pages
            section_path: list[str] | None = ref.section_path
            if text is None or pages is None or section_path is None:
                if allow_candidate_fallback and len(unique_refs) == 1:
                    text = candidate.text or ""
                    pages = list(candidate.pages)
                    section_path = list(candidate.section_path)
                else:
                    skipped.append(
                        f"skipped unbound-able source ref "
                        f"{ref.block_id!r} [{ref.char_start},{ref.char_end}) "
                        f"of candidate {eid!r} for field {field_id!r}: no "
                        "per-ref canonical provenance to bind (no fabricated "
                        "coordinates/quotes/pages)"
                    )
                    continue
            if not (
                0 <= ref.char_start <= ref.char_end <= len(text)
            ):
                skipped.append(
                    f"skipped unbound-able source ref "
                    f"{ref.block_id!r} [{ref.char_start},{ref.char_end}) "
                    f"of candidate {eid!r} for field {field_id!r}: char range "
                    f"is invalid for canonical text of length {len(text)}"
                )
                continue
            quote = text[ref.char_start:ref.char_end] if text else ""
            refs.append(
                EvidenceRef(
                    block_id=ref.block_id,
                    char_start=ref.char_start,
                    char_end=ref.char_end,
                    pages=list(pages),
                    section_path=list(section_path),
                    quote=quote,
                )
            )
    if skipped and warnings_out is not None:
        warnings_out.extend(skipped)
    if not refs and evidence_ids:
        raise EvidenceBindingError(
            f"field {field_id!r}: selected evidence could not be bound to any "
            "traceable canonical block; per-ref canonical provenance was "
            "unavailable for every selected ref after skipping unbound-able "
            "refs; refusing to return an assertive value with zero evidence",
            field_id=field_id,
        )
    return refs
