"""Paper-dedup + manual processing service.

Public interface frozen in Phase 1. All functions are deterministic and
side-effect-limited: detection only reads existing DB rows and writes
candidate relations; resolution / mutations only change the targeted
records and always append an audit_log entry.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select

from transit_scholar.config import settings
from transit_scholar.db.engine import SessionLocal
from transit_scholar.db.models import AuditLog, Paper, PaperAuthor, PaperFile, PaperRelation
from transit_scholar.identity.result import (
    DuplicateCandidateView,
    DuplicateDetectionResult,
    DuplicateResolutionResult,
    PaperActionResult,
)
from transit_scholar.identity.scoring import score_paper_pair
from transit_scholar.metadata import selection as metadata_selection
from transit_scholar.metadata.normalizers import (
    normalize_arxiv_id,
    normalize_doi,
    normalize_title,
)

# --- Error codes (frozen) ----------------------------------------------------
PAPER_NOT_FOUND = "PAPER_NOT_FOUND"
RELATION_NOT_FOUND = "RELATION_NOT_FOUND"
FILE_NOT_FOUND = "FILE_NOT_FOUND"
INVALID_DECISION = "INVALID_DECISION"
INVALID_FIELDS = "INVALID_FIELDS"
INVALID_STATE = "INVALID_STATE"
FILE_MOVE_FAILED = "FILE_MOVE_FAILED"
DATABASE_WRITE_FAILED = "DATABASE_WRITE_FAILED"

# --- Decision / field enumerations (frozen) ---------------------------------
DECISION_VALUES = ("same_paper", "different_version", "not_duplicate", "ignore")

UPDATEABLE_METADATA_FIELDS = frozenset(
    {"title", "abstract", "publication_year", "venue", "doi", "arxiv_id", "authors"}
)

# --- papers.status values (frozen) ------------------------------------------
STATUS_ACTIVE = "active"
STATUS_DUPLICATE_PENDING = "duplicate_pending"
STATUS_ARCHIVED = "archived"
STATUS_DELETED = "deleted"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _write_audit(
    session,
    *,
    entity_type: str,
    entity_id: str,
    action: str,
    actor_type: str,
    old_value: dict[str, Any] | None = None,
    new_value: dict[str, Any] | None = None,
) -> str:
    """Append an audit_log row and return its id."""
    log = AuditLog(
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        actor_type=actor_type,
        old_value_json=json.dumps(old_value, ensure_ascii=False) if old_value is not None else None,
        new_value_json=json.dumps(new_value, ensure_ascii=False) if new_value is not None else None,
    )
    session.add(log)
    session.flush()
    return log.id


def _pair_key(a: str, b: str) -> tuple[str, str]:
    """Canonical ordering of two paper ids so direction is stable."""
    return (a, b) if a <= b else (b, a)


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def detect_duplicate_candidates(
    paper_id: str, *, create_relations: bool = True
) -> DuplicateDetectionResult:
    """Generate candidate relations for ``paper_id`` against every other
    non-deleted paper in the database.
    """
    result = DuplicateDetectionResult(
        paper_id=paper_id,
        status="failed",
        candidates_seen=0,
        relations_created=0,
        relations_existing=0,
        relation_ids=[],
        error_code=None,
        error_message=None,
    )
    try:
        with SessionLocal() as session:
            paper = session.get(Paper, paper_id)
            if paper is None:
                result.error_code = PAPER_NOT_FOUND
                result.error_message = f"Paper not found: {paper_id}"
                return result
            if paper.status == STATUS_DELETED:
                result.error_code = INVALID_STATE
                result.error_message = f"Paper is deleted: {paper_id}"
                return result

            others = [
                p for p in session.execute(
                    select(Paper).where(
                        Paper.id != paper_id,
                        Paper.status != STATUS_DELETED,
                    )
                ).scalars().all()
            ]

            created = 0
            existing = 0
            relation_ids: list[str] = []
            touched_paper_ids: set[str] = set()

            for other in others:
                scored = score_paper_pair(paper, other)
                result.candidates_seen += 1

                relation_type = _classify(scored)
                if relation_type is None:
                    continue

                # Exact-match paths (DOI/arXiv/title) already pin confidence;
                # for threshold-classified fuzzy matches the confidence is the
                # raw composite score.
                confidence = scored["confidence"]
                if confidence is None:
                    confidence = scored["score"]
                reasons = scored["reasons"]
                src, tgt = _pair_key(paper.id, other.id)

                existing_rel = session.execute(
                    select(PaperRelation).where(
                        PaperRelation.source_paper_id == src,
                        PaperRelation.target_paper_id == tgt,
                        PaperRelation.relation_type == relation_type,
                    )
                ).scalar_one_or_none()

                if existing_rel is not None:
                    existing += 1
                    relation_ids.append(existing_rel.id)
                    continue

                if not create_relations:
                    continue

                rel = PaperRelation(
                    source_paper_id=src,
                    target_paper_id=tgt,
                    relation_type=relation_type,
                    confidence=confidence,
                    status="pending",
                    reasons_json=json.dumps(reasons, ensure_ascii=False),
                )
                session.add(rel)
                session.flush()
                created += 1
                relation_ids.append(rel.id)
                touched_paper_ids.add(other.id)

            if created > 0:
                # Mark involved active papers as duplicate_pending.
                for pid in touched_paper_ids | {paper.id}:
                    p = session.get(Paper, pid)
                    if p is not None and p.status == STATUS_ACTIVE:
                        p.status = STATUS_DUPLICATE_PENDING
                # Ensure the queried paper itself is marked.
                paper = session.get(Paper, paper_id)
                if paper.status == STATUS_ACTIVE:
                    paper.status = STATUS_DUPLICATE_PENDING

            session.commit()

        result.status = "completed"
        result.relations_created = created
        result.relations_existing = existing
        result.relation_ids = relation_ids
        return result
    except Exception as exc:  # noqa: BLE001
        result.error_code = DATABASE_WRITE_FAILED
        result.error_message = f"Detection failed: {exc}"
        return result


def _classify(scored: dict[str, Any]) -> str | None:
    """Map a score dict to a relation_type, or None if below weak threshold."""
    # DOI / arXiv exact matches already fixed relation_type + confidence.
    if scored.get("confidence") == 1.0 and scored.get("relation_type") == "exact_duplicate":
        return "exact_duplicate"
    if scored.get("relation_type") == "probable_duplicate":
        return "probable_duplicate"

    score = scored["score"]
    if score >= settings.duplicate_high_threshold:
        return "probable_duplicate"
    if score >= settings.duplicate_probable_threshold:
        return "possible_version"
    if score >= settings.duplicate_weak_threshold:
        return "related"
    return None


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------


def list_duplicate_candidates(
    paper_id: str, *, status: str | None = "pending"
) -> list[DuplicateCandidateView]:
    """Return relations involving ``paper_id``, optionally filtered by status."""
    with SessionLocal() as session:
        stmt = select(PaperRelation).where(
            (PaperRelation.source_paper_id == paper_id)
            | (PaperRelation.target_paper_id == paper_id)
        )
        if status is not None:
            stmt = stmt.where(PaperRelation.status == status)
        rows = session.execute(stmt.order_by(PaperRelation.confidence.desc())).scalars().all()

        views: list[DuplicateCandidateView] = []
        for r in rows:
            try:
                reasons = json.loads(r.reasons_json) if r.reasons_json else []
            except Exception:  # noqa: BLE001
                reasons = []
            views.append(DuplicateCandidateView(
                relation_id=r.id,
                source_paper_id=r.source_paper_id,
                target_paper_id=r.target_paper_id,
                relation_type=r.relation_type,
                confidence=r.confidence,
                status=r.status,
                reasons=reasons if isinstance(reasons, list) else [],
            ))
        return views


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def resolve_duplicate(
    relation_id: str,
    decision: str,
    *,
    actor_type: str = "local_user",
) -> DuplicateResolutionResult:
    """Apply a manual decision to a pending relation."""
    result = DuplicateResolutionResult(
        relation_id=relation_id,
        status="failed",
        decision=decision,
        audit_log_id=None,
        error_code=None,
        error_message=None,
    )
    if decision not in DECISION_VALUES:
        result.error_code = INVALID_DECISION
        result.error_message = f"Invalid decision: {decision!r}"
        return result

    try:
        with SessionLocal() as session:
            rel = session.get(PaperRelation, relation_id)
            if rel is None:
                result.error_code = RELATION_NOT_FOUND
                result.error_message = f"PaperRelation not found: {relation_id}"
                return result

            old_value = {
                "status": rel.status,
                "relation_type": rel.relation_type,
            }

            if decision == "same_paper":
                rel.status = "confirmed"
                # relation_type unchanged
            elif decision == "different_version":
                rel.status = "confirmed"
                rel.relation_type = "possible_version"
            elif decision == "not_duplicate":
                rel.status = "rejected"
            elif decision == "ignore":
                rel.status = "ignored"

            rel.resolved_at = _now()
            rel.resolved_by = actor_type

            audit_id = _write_audit(
                session,
                entity_type="paper_relation",
                entity_id=rel.id,
                action="resolve_duplicate",
                actor_type=actor_type,
                old_value=old_value,
                new_value={
                    "status": rel.status,
                    "relation_type": rel.relation_type,
                    "decision": decision,
                },
            )

            # Status convergence: if either endpoint has no more pending
            # relations and is currently duplicate_pending, restore to active.
            _converge_endpoint(session, rel.source_paper_id)
            _converge_endpoint(session, rel.target_paper_id)

            session.commit()

        result.status = "resolved"
        result.audit_log_id = audit_id
        return result
    except Exception as exc:  # noqa: BLE001
        result.error_code = DATABASE_WRITE_FAILED
        result.error_message = f"Resolution failed: {exc}"
        return result


def _converge_endpoint(session, paper_id: str) -> None:
    """Restore a duplicate_pending paper to active when it has no pending relations."""
    paper = session.get(Paper, paper_id)
    if paper is None:
        return
    if paper.status != STATUS_DUPLICATE_PENDING:
        return
    pending = session.execute(
        select(PaperRelation).where(
            PaperRelation.status == "pending",
            (PaperRelation.source_paper_id == paper_id)
            | (PaperRelation.target_paper_id == paper_id),
        )
    ).scalars().all()
    if not pending:
        paper.status = STATUS_ACTIVE


# ---------------------------------------------------------------------------
# Metadata update
# ---------------------------------------------------------------------------


def update_paper_metadata(
    paper_id: str,
    fields: dict[str, object],
    *,
    actor_type: str = "local_user",
) -> PaperActionResult:
    """Update whitelisted metadata fields, syncing normalised columns.

    Every non-empty value is first persisted as a ``manual_confirmed``
    candidate (source_location=user_edit) on the paper's primary file when one
    exists, then the deterministic selection service re-runs and materializes
    the selection. The confirmed values therefore stay selected across session
    close/reopen and forced provider refreshes, and they can never be replaced
    by DOI provider values. ``authors`` is accepted as an ordered list of
    non-empty names and is persisted as the aggregate canonical JSON array.
    Papers without a primary file keep the legacy direct-write behavior. The
    existing audit log is always appended.
    """
    result = PaperActionResult(
        paper_id=paper_id,
        status="failed",
        updated_fields=[],
        audit_log_id=None,
        error_code=None,
        error_message=None,
    )
    if not fields:
        result.error_code = INVALID_FIELDS
        result.error_message = "No fields supplied"
        return result
    unknown = set(fields) - UPDATEABLE_METADATA_FIELDS
    if unknown:
        result.error_code = INVALID_FIELDS
        result.error_message = f"Non-updatable fields: {sorted(unknown)}"
        return result
    if "authors" in fields:
        authors = fields["authors"]
        if not isinstance(authors, (list, tuple)):
            result.error_code = INVALID_FIELDS
            result.error_message = "authors must be an ordered list of non-empty names"
            return result
        names = [str(n).strip() for n in authors if n is not None and str(n).strip()]
        if not names:
            result.error_code = INVALID_FIELDS
            result.error_message = "authors must be a non-empty list of non-empty names"
            return result
        fields = dict(fields)
        fields["authors"] = names

    try:
        with SessionLocal() as session:
            paper = session.get(Paper, paper_id)
            if paper is None:
                result.error_code = PAPER_NOT_FOUND
                result.error_message = f"Paper not found: {paper_id}"
                return result

            old: dict[str, Any] = {}
            new: dict[str, Any] = {}

            for field in fields:
                if field == "authors":
                    old[field] = _author_names(session, paper.id)
                else:
                    old[field] = getattr(paper, field)
                result.updated_fields.append(field)

            try:
                primary = metadata_selection.get_primary_file(session, paper.id)
            except metadata_selection.NoPrimaryFileError:
                primary = None

            if primary is not None:
                metadata_selection.persist_manual_candidates(
                    session, paper, primary, fields
                )
                # The engine uses autoflush=False, so the fresh candidates must
                # be flushed before selection can see them.
                session.flush()
                metadata_selection.reselect_and_materialize(session, paper)
                session.flush()
            else:
                _apply_manual_fields(session, paper, fields)
                session.flush()

            for field in fields:
                if field == "authors":
                    new[field] = _author_names(session, paper.id)
                elif field == "title":
                    new["title"] = paper.title
                    new["normalized_title"] = paper.normalized_title
                elif field == "doi":
                    new["doi"] = paper.doi
                    new["normalized_doi"] = paper.normalized_doi
                elif field == "arxiv_id":
                    new["arxiv_id"] = paper.arxiv_id
                else:
                    new[field] = getattr(paper, field)

            audit_id = _write_audit(
                session,
                entity_type="paper",
                entity_id=paper.id,
                action="update_metadata",
                actor_type=actor_type,
                old_value=old,
                new_value=new,
            )
            session.commit()

        result.status = "updated"
        result.audit_log_id = audit_id
        return result
    except Exception as exc:  # noqa: BLE001
        result.error_code = DATABASE_WRITE_FAILED
        result.error_message = f"Metadata update failed: {exc}"
        return result


def _apply_manual_fields(session, paper: Paper, fields: dict[str, object]) -> None:
    """Legacy direct write used when the paper has no primary file."""
    for field, value in fields.items():
        if field == "title":
            paper.title = value
            paper.normalized_title = normalize_title(value) if value else None
        elif field == "doi":
            paper.doi = value
            paper.normalized_doi = normalize_doi(value) if value else None
        elif field == "arxiv_id":
            paper.arxiv_id = normalize_arxiv_id(value) if value else None
        elif field == "authors":
            metadata_selection.replace_paper_authors(session, paper, list(value))
        else:
            setattr(paper, field, value)


def _author_names(session, paper_id: str) -> list[str]:
    """Current ordered full names of a paper's authors."""
    rows = session.execute(
        select(PaperAuthor)
        .where(PaperAuthor.paper_id == paper_id)
        .order_by(PaperAuthor.author_order)
    ).scalars().all()
    return [a.full_name for a in rows]


# ---------------------------------------------------------------------------
# Primary file
# ---------------------------------------------------------------------------


def set_primary_file(
    paper_id: str,
    file_id: str,
    *,
    actor_type: str = "local_user",
) -> PaperActionResult:
    """Set ``file_id`` as the sole primary file for its paper."""
    result = PaperActionResult(
        paper_id=paper_id,
        status="failed",
        updated_fields=[],
        audit_log_id=None,
        error_code=None,
        error_message=None,
    )
    try:
        with SessionLocal() as session:
            paper = session.get(Paper, paper_id)
            if paper is None:
                result.error_code = PAPER_NOT_FOUND
                result.error_message = f"Paper not found: {paper_id}"
                return result

            pf = session.get(PaperFile, file_id)
            if pf is None or pf.paper_id != paper_id:
                result.error_code = INVALID_FIELDS
                result.error_message = f"File {file_id} does not belong to paper {paper_id}"
                return result
            if pf.deleted_at is not None:
                result.error_code = INVALID_STATE
                result.error_message = f"File {file_id} is deleted"
                return result

            old_primary: str | None = None
            for f in session.execute(
                select(PaperFile).where(PaperFile.paper_id == paper_id)
            ).scalars().all():
                if f.is_primary and f.id != file_id:
                    f.is_primary = False
                    old_primary = f.id
            pf.is_primary = True

            audit_id = _write_audit(
                session,
                entity_type="paper_file",
                entity_id=file_id,
                action="set_primary_file",
                actor_type=actor_type,
                old_value={"previous_primary_file_id": old_primary},
                new_value={"primary_file_id": file_id, "paper_id": paper_id},
            )
            session.commit()

        result.status = "updated"
        result.updated_fields = ["is_primary"]
        result.audit_log_id = audit_id
        return result
    except Exception as exc:  # noqa: BLE001
        result.error_code = DATABASE_WRITE_FAILED
        result.error_message = f"Set primary file failed: {exc}"
        return result


# ---------------------------------------------------------------------------
# Archive
# ---------------------------------------------------------------------------


def archive_paper(
    paper_id: str,
    *,
    actor_type: str = "local_user",
) -> PaperActionResult:
    """Archive a paper: set status=archived, no file moves."""
    result = PaperActionResult(
        paper_id=paper_id,
        status="failed",
        updated_fields=[],
        audit_log_id=None,
        error_code=None,
        error_message=None,
    )
    try:
        with SessionLocal() as session:
            paper = session.get(Paper, paper_id)
            if paper is None:
                result.error_code = PAPER_NOT_FOUND
                result.error_message = f"Paper not found: {paper_id}"
                return result

            old_status = paper.status
            paper.status = STATUS_ARCHIVED

            audit_id = _write_audit(
                session,
                entity_type="paper",
                entity_id=paper_id,
                action="archive_paper",
                actor_type=actor_type,
                old_value={"status": old_status},
                new_value={"status": STATUS_ARCHIVED},
            )
            session.commit()

        result.status = "archived"
        result.updated_fields = ["status"]
        result.audit_log_id = audit_id
        return result
    except Exception as exc:  # noqa: BLE001
        result.error_code = DATABASE_WRITE_FAILED
        result.error_message = f"Archive failed: {exc}"
        return result


# ---------------------------------------------------------------------------
# Soft delete
# ---------------------------------------------------------------------------


def soft_delete_paper(
    paper_id: str,
    *,
    actor_type: str = "local_user",
    move_files: bool = True,
) -> PaperActionResult:
    """Soft-delete a paper: set status, stamp deleted_at, optionally move files to trash."""
    result = PaperActionResult(
        paper_id=paper_id,
        status="failed",
        updated_fields=[],
        audit_log_id=None,
        error_code=None,
        error_message=None,
    )
    try:
        with SessionLocal() as session:
            paper = session.get(Paper, paper_id)
            if paper is None:
                result.error_code = PAPER_NOT_FOUND
                result.error_message = f"Paper not found: {paper_id}"
                return result

            files = session.execute(
                select(PaperFile).where(
                    PaperFile.paper_id == paper_id,
                    PaperFile.deleted_at.is_(None),
                )
            ).scalars().all()

            # Verify files exist on disk before mutating DB state when we
            # intend to move them.
            if move_files:
                for pf in files:
                    if pf.relative_path:
                        disk = Path(settings.data_root) / pf.relative_path
                        if not disk.is_file():
                            result.error_code = FILE_NOT_FOUND
                            result.error_message = f"File missing on disk: {disk}"
                            return result

            old_status = paper.status
            paper.status = STATUS_DELETED
            paper.deleted_at = _now()

            moved: list[str] = []
            # Track (original_src, dst) for every successful move so that a
            # later failure can move them back — otherwise the DB rollback
            # would leave the DB pointing at ``originals`` while the file
            # physically sits in ``trash``.
            completed_moves: list[tuple[Path, Path]] = []
            try:
                for pf in files:
                    pf.deleted_at = _now()
                    if move_files and pf.relative_path:
                        src = Path(settings.data_root) / pf.relative_path
                        new_rel = f"library/trash/{pf.id}/source.pdf"
                        dst = Path(settings.data_root) / new_rel
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(src), str(dst))
                        pf.relative_path = new_rel
                        moved.append(pf.id)
                        completed_moves.append((src, dst))
            except Exception as exc:  # noqa: BLE001
                # Compensation: move already-moved files back to their
                # original locations (best-effort, newest-first).
                comp_errors: list[str] = []
                for orig_src, dst in reversed(completed_moves):
                    try:
                        shutil.move(str(dst), str(orig_src))
                    except Exception as comp_exc:  # noqa: BLE001
                        comp_errors.append(f"{dst} -> {orig_src}: {comp_exc}")
                session.rollback()
                result.error_code = FILE_MOVE_FAILED
                msg = f"File move failed during soft delete: {exc}"
                if comp_errors:
                    msg += f" (compensation errors: {'; '.join(comp_errors)})"
                result.error_message = msg
                return result

            audit_id = _write_audit(
                session,
                entity_type="paper",
                entity_id=paper_id,
                action="soft_delete_paper",
                actor_type=actor_type,
                old_value={"status": old_status},
                new_value={
                    "status": STATUS_DELETED,
                    "deleted_at": paper.deleted_at.isoformat(),
                    "moved_file_ids": moved,
                },
            )
            session.commit()

        result.status = "deleted"
        result.updated_fields = ["status", "deleted_at"]
        result.audit_log_id = audit_id
        return result
    except Exception as exc:  # noqa: BLE001
        result.error_code = DATABASE_WRITE_FAILED
        result.error_message = f"Soft delete failed: {exc}"
        return result


# ---------------------------------------------------------------------------
# Restore
# ---------------------------------------------------------------------------


def restore_paper(
    paper_id: str,
    *,
    actor_type: str = "local_user",
    move_files: bool = True,
) -> PaperActionResult:
    """Restore a deleted (or archived) paper to active, moving files back if present."""
    result = PaperActionResult(
        paper_id=paper_id,
        status="failed",
        updated_fields=[],
        audit_log_id=None,
        error_code=None,
        error_message=None,
    )
    try:
        with SessionLocal() as session:
            paper = session.get(Paper, paper_id)
            if paper is None:
                result.error_code = PAPER_NOT_FOUND
                result.error_message = f"Paper not found: {paper_id}"
                return result
            if paper.status not in (STATUS_DELETED, STATUS_ARCHIVED):
                result.error_code = INVALID_STATE
                result.error_message = f"Paper status is not restorable: {paper.status}"
                return result

            files = session.execute(
                select(PaperFile).where(PaperFile.paper_id == paper_id)
            ).scalars().all()

            # When moving files back, verify the trash copy exists first.
            if move_files:
                for pf in files:
                    if pf.relative_path and pf.relative_path.startswith("library/trash/"):
                        disk = Path(settings.data_root) / pf.relative_path
                        if not disk.is_file():
                            result.error_code = FILE_NOT_FOUND
                            result.error_message = f"Trash file missing on disk: {disk}"
                            return result

            old_status = paper.status
            paper.status = STATUS_ACTIVE
            paper.deleted_at = None

            restored: list[str] = []
            # Track (trash_src, originals_dst) for every successful move so
            # that a later failure can move them back — otherwise the DB
            # rollback would leave the DB pointing at ``trash`` while the
            # file physically sits in ``originals``.
            completed_moves: list[tuple[Path, Path]] = []
            try:
                for pf in files:
                    was_deleted = pf.deleted_at is not None
                    pf.deleted_at = None
                    if (
                        move_files
                        and pf.relative_path
                        and pf.relative_path.startswith("library/trash/")
                    ):
                        src = Path(settings.data_root) / pf.relative_path
                        new_rel = f"library/originals/{pf.id}/source.pdf"
                        dst = Path(settings.data_root) / new_rel
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(src), str(dst))
                        pf.relative_path = new_rel
                        restored.append(pf.id)
                        completed_moves.append((src, dst))
                    elif was_deleted:
                        restored.append(pf.id)
            except Exception as exc:  # noqa: BLE001
                # Compensation: move already-restored files back to trash
                # (best-effort, newest-first).
                comp_errors: list[str] = []
                for trash_src, originals_dst in reversed(completed_moves):
                    try:
                        shutil.move(str(originals_dst), str(trash_src))
                    except Exception as comp_exc:  # noqa: BLE001
                        comp_errors.append(
                            f"{originals_dst} -> {trash_src}: {comp_exc}"
                        )
                session.rollback()
                result.error_code = FILE_MOVE_FAILED
                msg = f"File move failed during restore: {exc}"
                if comp_errors:
                    msg += f" (compensation errors: {'; '.join(comp_errors)})"
                result.error_message = msg
                return result

            audit_id = _write_audit(
                session,
                entity_type="paper",
                entity_id=paper_id,
                action="restore_paper",
                actor_type=actor_type,
                old_value={"status": old_status},
                new_value={
                    "status": STATUS_ACTIVE,
                    "restored_file_ids": restored,
                },
            )
            session.commit()

        result.status = "restored"
        result.updated_fields = ["status", "deleted_at"]
        result.audit_log_id = audit_id
        return result
    except Exception as exc:  # noqa: BLE001
        result.error_code = DATABASE_WRITE_FAILED
        result.error_message = f"Restore failed: {exc}"
        return result
