"""Maintenance service: list and preview maintenance items.

Read-only by design. ``list_maintenance_items`` scans the database and the
disk to surface maintenance concerns; ``preview_maintenance_action`` describes
what an action *would* do. Neither function deletes files, moves files, or
writes to the database.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from transit_scholar.config import settings
from transit_scholar.db.engine import SessionLocal
from transit_scholar.db.models import (
    IngestionJob,
    Paper,
    PaperFile,
)
from transit_scholar.maintenance.result import (
    MaintenanceItem,
    MaintenancePreviewResult,
)

# --- item_type constants ---------------------------------------------------
FAILED_INGESTION_JOB = "failed_ingestion_job"
TEMPORARY_RESIDUE = "temporary_residue"
SOFT_DELETED_PAPER = "soft_deleted_paper"
SOFT_DELETED_FILE = "soft_deleted_file"
MISSING_ORIGINAL_FILE = "missing_original_file"
ORPHAN_TEMPORARY_DIR = "orphan_temporary_dir"
ORPHAN_TRASH_FILE = "orphan_trash_file"

# --- action constants ------------------------------------------------------
PURGE_TEMPORARY_PATH = "purge_temporary_path"
PURGE_TRASH_PATH = "purge_trash_path"
PURGE_DELETED_ASSET = "purge_deleted_asset"
RETRY_IMPORT = "retry_import"
MANUAL_PROMOTE = "manual_promote"
RESTORE = "restore"
RECONCILE_MISSING_ORIGINAL = "reconcile_missing_original"

SAFE_ACTIONS = [PURGE_TEMPORARY_PATH, PURGE_TRASH_PATH, RETRY_IMPORT, RESTORE]
DANGEROUS_ACTIONS = [PURGE_DELETED_ASSET, MANUAL_PROMOTE, RECONCILE_MISSING_ORIGINAL]

_RECOMMENDED: dict[str, list[str]] = {
    FAILED_INGESTION_JOB: [RETRY_IMPORT, PURGE_TEMPORARY_PATH],
    TEMPORARY_RESIDUE: [PURGE_TEMPORARY_PATH],
    SOFT_DELETED_PAPER: [RESTORE, PURGE_DELETED_ASSET],
    SOFT_DELETED_FILE: [RESTORE, PURGE_DELETED_ASSET],
    MISSING_ORIGINAL_FILE: [RECONCILE_MISSING_ORIGINAL],
    ORPHAN_TEMPORARY_DIR: [PURGE_TEMPORARY_PATH],
    ORPHAN_TRASH_FILE: [PURGE_TRASH_PATH],
}

_SAFE: dict[str, list[str]] = {
    FAILED_INGESTION_JOB: [RETRY_IMPORT, PURGE_TEMPORARY_PATH],
    TEMPORARY_RESIDUE: [PURGE_TEMPORARY_PATH],
    SOFT_DELETED_PAPER: [RESTORE],
    SOFT_DELETED_FILE: [RESTORE],
    MISSING_ORIGINAL_FILE: [],
    ORPHAN_TEMPORARY_DIR: [PURGE_TEMPORARY_PATH],
    ORPHAN_TRASH_FILE: [PURGE_TRASH_PATH],
}

_DANGEROUS: dict[str, list[str]] = {
    FAILED_INGESTION_JOB: [],
    TEMPORARY_RESIDUE: [],
    SOFT_DELETED_PAPER: [PURGE_DELETED_ASSET],
    SOFT_DELETED_FILE: [PURGE_DELETED_ASSET],
    MISSING_ORIGINAL_FILE: [RECONCILE_MISSING_ORIGINAL],
    ORPHAN_TEMPORARY_DIR: [],
    ORPHAN_TRASH_FILE: [],
}

_SEVERITY: dict[str, str] = {
    FAILED_INGESTION_JOB: "warning",
    TEMPORARY_RESIDUE: "info",
    SOFT_DELETED_PAPER: "warning",
    SOFT_DELETED_FILE: "warning",
    MISSING_ORIGINAL_FILE: "critical",
    ORPHAN_TEMPORARY_DIR: "info",
    ORPHAN_TRASH_FILE: "info",
}

_RISK: dict[str, str] = {
    FAILED_INGESTION_JOB: "medium",
    TEMPORARY_RESIDUE: "low",
    SOFT_DELETED_PAPER: "high",
    SOFT_DELETED_FILE: "high",
    MISSING_ORIGINAL_FILE: "high",
    ORPHAN_TEMPORARY_DIR: "low",
    ORPHAN_TRASH_FILE: "low",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _abs(path: Path) -> str:
    return str(path.resolve())


def _item(
    *,
    item_id: str,
    item_type: str,
    title: str,
    description: str,
    related_job_id: str | None = None,
    related_paper_id: str | None = None,
    related_file_id: str | None = None,
    paths: list[str] | None = None,
    can_purge: bool = False,
    can_retry_import: bool = False,
    can_manual_promote: bool = False,
    can_restore: bool = False,
    requires_user_input: bool = False,
    blockers: list[str] | None = None,
) -> MaintenanceItem:
    return MaintenanceItem(
        item_id=item_id,
        item_type=item_type,
        severity=_SEVERITY[item_type],
        title=title,
        description=description,
        related_job_id=related_job_id,
        related_paper_id=related_paper_id,
        related_file_id=related_file_id,
        paths=paths or [],
        detected_at=_now(),
        can_purge=can_purge,
        can_retry_import=can_retry_import,
        can_manual_promote=can_manual_promote,
        can_restore=can_restore,
        requires_user_input=requires_user_input,
        risk_level=_RISK[item_type],
        recommended_actions=list(_RECOMMENDED[item_type]),
        safe_actions=list(_SAFE[item_type]),
        dangerous_actions=list(_DANGEROUS[item_type]),
        blockers=blockers or [],
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def list_maintenance_items() -> list[MaintenanceItem]:
    """Scan the database and disk, return every detected maintenance item.

    Read-only: does not mutate any state or filesystem content.
    """
    items: list[MaintenanceItem] = []
    items.extend(_list_failed_jobs())
    items.extend(_list_temporary_residue())
    items.extend(_list_soft_deleted_papers())
    items.extend(_list_soft_deleted_files())
    items.extend(_list_missing_originals())
    items.extend(_list_orphan_trash())
    return items


def get_maintenance_item(item_id: str) -> MaintenanceItem | None:
    """Return the maintenance item with the given id, or None."""
    for item in list_maintenance_items():
        if item.item_id == item_id:
            return item
    return None


def preview_maintenance_action(
    item_id: str, action: str
) -> MaintenancePreviewResult:
    """Return a preview of what ``action`` would do for the item.

    Read-only: never deletes files or writes to the database.
    """
    item = get_maintenance_item(item_id)
    if item is None:
        return MaintenancePreviewResult(
            item_id=item_id,
            action=action,
            allowed=False,
            risk_level="high",
            requires_confirmation=False,
            requires_user_input=False,
            affected_paths=[],
            affected_db_records=[],
            will_delete_paths=[],
            will_update_records=[],
            will_create_records=[],
            blockers=["item_not_found"],
            message=f"No maintenance item found for id: {item_id}",
        )

    allowed_actions = item.safe_actions + item.dangerous_actions
    if action not in allowed_actions:
        return MaintenancePreviewResult(
            item_id=item_id,
            action=action,
            allowed=False,
            risk_level=item.risk_level,
            requires_confirmation=False,
            requires_user_input=False,
            affected_paths=[],
            affected_db_records=[],
            will_delete_paths=[],
            will_update_records=[],
            will_create_records=[],
            blockers=["action_not_applicable_to_item_type"],
            message=f"Action {action!r} is not applicable to {item.item_type}",
        )

    return _preview_for_action(item, action)


# ---------------------------------------------------------------------------
# Detection helpers
# ---------------------------------------------------------------------------


def _list_failed_jobs() -> list[MaintenanceItem]:
    with SessionLocal() as session:
        rows = session.execute(
            select(IngestionJob).where(IngestionJob.status == "failed")
        ).scalars().all()

    items: list[MaintenanceItem] = []
    for job in rows:
        temp_dir = settings.temporary_dir / job.id
        paths = [_abs(p) for p in temp_dir.rglob("*") if p.is_file()]
        if not paths and temp_dir.exists():
            paths = [_abs(temp_dir)]

        source_accessible = (
            job.source_path is not None and Path(job.source_path).is_file()
        )
        blockers = []
        if not source_accessible:
            blockers.append("source_path_unavailable")

        items.append(_item(
            item_id=f"ingestion:{job.id}",
            item_type=FAILED_INGESTION_JOB,
            title=f"Failed import job: {job.uploaded_filename or job.id}",
            description=(
                f"Ingestion job {job.id} failed"
                f"{f' with error: {job.error_message}' if job.error_message else ''}"
            ),
            related_job_id=job.id,
            related_paper_id=job.paper_id,
            related_file_id=job.file_id,
            paths=paths,
            can_purge=True,
            can_retry_import=source_accessible,
            blockers=blockers,
        ))
    return items


def _list_temporary_residue() -> list[MaintenanceItem]:
    """Report temporary dirs that trace to a failed job, and orphan temp dirs.

    Mutual exclusion: a temporary path is reported as temporary_residue if it
    traces to a failed job, otherwise as orphan_temporary_dir. A path is never
    reported as both.
    """
    items: list[MaintenanceItem] = []
    temp_root = settings.temporary_dir
    if not temp_root.is_dir():
        return items

    # Collect candidate job ids from the database once.
    with SessionLocal() as session:
        all_jobs = session.execute(
            select(IngestionJob.id, IngestionJob.status)
        ).all()
    job_status = {jid: status for jid, status in all_jobs}
    failed_ids = {jid for jid, status in job_status.items() if status == "failed"}

    # Group residual paths by the immediate child of temporary/.
    children = sorted(
        p for p in temp_root.iterdir() if p.is_dir() or p.is_file()
    )
    for child in children:
        relative = child.relative_to(temp_root)
        paths = [_abs(p) for p in child.rglob("*") if p.is_file()]
        if not paths:
            paths = [_abs(child)]

        if child.is_dir() and child.name in failed_ids:
            items.append(_item(
                item_id=f"temp_residue:{relative}",
                item_type=TEMPORARY_RESIDUE,
                title=f"Temporary residue for failed job: {child.name}",
                description=(
                    "Temporary files remain for a failed ingestion job."
                ),
                related_job_id=child.name,
                paths=paths,
                can_purge=True,
            ))
        else:
            items.append(_item(
                item_id=f"orphan_temp:{relative}",
                item_type=ORPHAN_TEMPORARY_DIR,
                title=f"Orphan temporary path: {relative}",
                description=(
                    "Temporary path with no associated ingestion job."
                ),
                paths=paths,
                can_purge=True,
            ))
    return items


def _list_soft_deleted_papers() -> list[MaintenanceItem]:
    with SessionLocal() as session:
        rows = session.execute(
            select(Paper).where(Paper.deleted_at.is_not(None))
        ).scalars().all()

    items: list[MaintenanceItem] = []
    for paper in rows:
        items.append(_item(
            item_id=f"paper:{paper.id}",
            item_type=SOFT_DELETED_PAPER,
            title=f"Soft-deleted paper: {paper.title or paper.id}",
            description=f"Paper {paper.id} was soft-deleted.",
            related_paper_id=paper.id,
            can_purge=True,
            can_restore=True,
        ))
    return items


def _list_soft_deleted_files() -> list[MaintenanceItem]:
    with SessionLocal() as session:
        rows = session.execute(
            select(PaperFile).where(PaperFile.deleted_at.is_not(None))
        ).scalars().all()

    items: list[MaintenanceItem] = []
    for pf in rows:
        paths = []
        if pf.relative_path:
            disk = settings.data_root / pf.relative_path
            paths = [_abs(disk)]
        items.append(_item(
            item_id=f"file:{pf.id}",
            item_type=SOFT_DELETED_FILE,
            title=f"Soft-deleted file: {pf.original_filename or pf.id}",
            description=f"Paper file {pf.id} was soft-deleted.",
            related_paper_id=pf.paper_id,
            related_file_id=pf.id,
            paths=paths,
            can_purge=True,
            can_restore=True,
        ))
    return items


def _list_missing_originals() -> list[MaintenanceItem]:
    """Report paper_files whose relative_path target is missing on disk."""
    with SessionLocal() as session:
        rows = session.execute(
            select(PaperFile).where(PaperFile.relative_path.is_not(None))
        ).scalars().all()

    items: list[MaintenanceItem] = []
    for pf in rows:
        disk = settings.data_root / pf.relative_path
        if disk.is_file():
            continue
        items.append(_item(
            item_id=f"missing:{pf.id}",
            item_type=MISSING_ORIGINAL_FILE,
            title=f"Missing original file: {pf.original_filename or pf.id}",
            description=(
                f"Database references {pf.relative_path} but the file is "
                f"not present on disk."
            ),
            related_paper_id=pf.paper_id,
            related_file_id=pf.id,
            paths=[_abs(disk)],
            can_purge=True,
            requires_user_input=True,
            blockers=["requires_user_input"],
        ))
    return items


def _list_orphan_trash() -> list[MaintenanceItem]:
    """Report trash paths that have no soft-deleted record tracking them."""
    trash_root = settings.trash_dir
    if not trash_root.is_dir():
        return []

    # Build the set of soft-deleted file/paper ids for linkage.
    with SessionLocal() as session:
        deleted_files = session.execute(
            select(PaperFile.id).where(PaperFile.deleted_at.is_not(None))
        ).scalars().all()
        deleted_papers = session.execute(
            select(Paper.id).where(Paper.deleted_at.is_not(None))
        ).scalars().all()
    tracked = set(deleted_files) | set(deleted_papers)

    items: list[MaintenanceItem] = []
    children = sorted(p for p in trash_root.iterdir())
    for child in children:
        relative = child.relative_to(trash_root)
        paths = [_abs(p) for p in child.rglob("*") if p.is_file()]
        if not paths:
            paths = [_abs(child)]
        # A trash path is orphan if its immediate name is not a tracked id.
        is_orphan = child.name not in tracked
        if is_orphan:
            items.append(_item(
                item_id=f"orphan_trash:{relative}",
                item_type=ORPHAN_TRASH_FILE,
                title=f"Orphan trash path: {relative}",
                description="Trash path with no soft-deleted record tracking it.",
                paths=paths,
                can_purge=True,
            ))
    return items


# ---------------------------------------------------------------------------
# Preview helpers
# ---------------------------------------------------------------------------


def _preview_for_action(
    item: MaintenanceItem, action: str,
) -> MaintenancePreviewResult:
    """Build the preview result for an action applicable to the item."""
    strategy = _PREVIEW_STRATEGY.get((item.item_type, action))
    if strategy is not None:
        return strategy(item)

    # Fallback: allowed preview with generic description.
    return MaintenancePreviewResult(
        item_id=item.item_id,
        action=action,
        allowed=True,
        risk_level=item.risk_level,
        requires_confirmation=item.risk_level == "high",
        requires_user_input=False,
        affected_paths=list(item.paths),
        affected_db_records=[],
        will_delete_paths=[],
        will_update_records=[],
        will_create_records=[],
        blockers=[],
        message=f"Preview of {action} on {item.item_type}.",
    )


def _purge_temporary_preview(item: MaintenanceItem) -> MaintenancePreviewResult:
    return MaintenancePreviewResult(
        item_id=item.item_id,
        action=PURGE_TEMPORARY_PATH,
        allowed=True,
        risk_level="low",
        requires_confirmation=False,
        requires_user_input=False,
        affected_paths=list(item.paths),
        affected_db_records=[],
        will_delete_paths=list(item.paths),
        will_update_records=[],
        will_create_records=[],
        blockers=[],
        message="Would delete the temporary path(s).",
    )


def _purge_trash_preview(item: MaintenanceItem) -> MaintenancePreviewResult:
    return MaintenancePreviewResult(
        item_id=item.item_id,
        action=PURGE_TRASH_PATH,
        allowed=True,
        risk_level="low",
        requires_confirmation=False,
        requires_user_input=False,
        affected_paths=list(item.paths),
        affected_db_records=[],
        will_delete_paths=list(item.paths),
        will_update_records=[],
        will_create_records=[],
        blockers=[],
        message="Would delete the trash path(s).",
    )


def _retry_import_preview(item: MaintenanceItem) -> MaintenancePreviewResult:
    if "source_path_unavailable" in item.blockers:
        return MaintenancePreviewResult(
            item_id=item.item_id,
            action=RETRY_IMPORT,
            allowed=False,
            risk_level="medium",
            requires_confirmation=False,
            requires_user_input=False,
            affected_paths=[],
            affected_db_records=[f"ingestion_jobs:{item.related_job_id}"],
            will_delete_paths=[],
            will_update_records=[],
            will_create_records=[],
            blockers=["source_path_unavailable"],
            message="Cannot retry: source file is not accessible.",
        )
    return MaintenancePreviewResult(
        item_id=item.item_id,
        action=RETRY_IMPORT,
        allowed=True,
        risk_level="medium",
        requires_confirmation=False,
        requires_user_input=False,
        affected_paths=[],
        affected_db_records=[f"ingestion_jobs:{item.related_job_id}"],
        will_delete_paths=[],
        will_update_records=[f"ingestion_jobs:{item.related_job_id}"],
        will_create_records=[],
        blockers=[],
        message="Would retry the failed import job.",
    )


def _restore_preview(item: MaintenanceItem) -> MaintenancePreviewResult:
    db_records = []
    if item.related_paper_id:
        db_records.append(f"papers:{item.related_paper_id}")
    if item.related_file_id:
        db_records.append(f"paper_files:{item.related_file_id}")
    return MaintenancePreviewResult(
        item_id=item.item_id,
        action=RESTORE,
        allowed=True,
        risk_level="medium",
        requires_confirmation=False,
        requires_user_input=False,
        affected_paths=list(item.paths),
        affected_db_records=db_records,
        will_delete_paths=[],
        will_update_records=db_records,
        will_create_records=[],
        blockers=[],
        message="Would restore the soft-deleted record.",
    )


def _purge_deleted_asset_preview(
    item: MaintenanceItem,
) -> MaintenancePreviewResult:
    db_records = []
    if item.related_paper_id:
        db_records.append(f"papers:{item.related_paper_id}")
    if item.related_file_id:
        db_records.append(f"paper_files:{item.related_file_id}")
    return MaintenancePreviewResult(
        item_id=item.item_id,
        action=PURGE_DELETED_ASSET,
        allowed=True,
        risk_level="high",
        requires_confirmation=True,
        requires_user_input=False,
        affected_paths=list(item.paths),
        affected_db_records=db_records,
        will_delete_paths=list(item.paths),
        will_update_records=[],
        will_create_records=[],
        blockers=[],
        message="Would permanently delete the soft-deleted asset and its files.",
    )


def _manual_promote_preview(item: MaintenanceItem) -> MaintenancePreviewResult:
    db_records = [f"ingestion_jobs:{item.related_job_id}"]
    if item.related_paper_id:
        db_records.append(f"papers:{item.related_paper_id}")
    return MaintenancePreviewResult(
        item_id=item.item_id,
        action=MANUAL_PROMOTE,
        allowed=True,
        risk_level="high",
        requires_confirmation=True,
        requires_user_input=False,
        affected_paths=list(item.paths),
        affected_db_records=db_records,
        will_delete_paths=[],
        will_update_records=db_records,
        will_create_records=[],
        blockers=[],
        message="Would manually promote the partial/failed asset.",
    )


def _reconcile_missing_preview(
    item: MaintenanceItem,
) -> MaintenancePreviewResult:
    db_records = []
    if item.related_paper_id:
        db_records.append(f"papers:{item.related_paper_id}")
    if item.related_file_id:
        db_records.append(f"paper_files:{item.related_file_id}")
    return MaintenancePreviewResult(
        item_id=item.item_id,
        action=RECONCILE_MISSING_ORIGINAL,
        allowed=False,
        risk_level="high",
        requires_confirmation=True,
        requires_user_input=True,
        affected_paths=list(item.paths),
        affected_db_records=db_records,
        will_delete_paths=[],
        will_update_records=[],
        will_create_records=[],
        blockers=["requires_user_input"],
        message="Requires user input to reconcile the missing original file.",
    )


# Mapping (item_type, action) -> preview builder.
_PREVIEW_STRATEGY: dict[tuple[str, str], callable] = {
    (FAILED_INGESTION_JOB, RETRY_IMPORT): _retry_import_preview,
    (FAILED_INGESTION_JOB, PURGE_TEMPORARY_PATH): _purge_temporary_preview,
    (FAILED_INGESTION_JOB, MANUAL_PROMOTE): _manual_promote_preview,
    (TEMPORARY_RESIDUE, PURGE_TEMPORARY_PATH): _purge_temporary_preview,
    (SOFT_DELETED_PAPER, RESTORE): _restore_preview,
    (SOFT_DELETED_PAPER, PURGE_DELETED_ASSET): _purge_deleted_asset_preview,
    (SOFT_DELETED_FILE, RESTORE): _restore_preview,
    (SOFT_DELETED_FILE, PURGE_DELETED_ASSET): _purge_deleted_asset_preview,
    (MISSING_ORIGINAL_FILE, RECONCILE_MISSING_ORIGINAL): _reconcile_missing_preview,
    (ORPHAN_TEMPORARY_DIR, PURGE_TEMPORARY_PATH): _purge_temporary_preview,
    (ORPHAN_TRASH_FILE, PURGE_TRASH_PATH): _purge_trash_preview,
}
