"""Return structures for the maintenance package.

MaintenanceItem describes a single detected maintenance concern.
MaintenancePreviewResult describes what a maintenance action *would* do,
without executing it. No business logic lives here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class MaintenanceItem:
    """A single detected maintenance concern."""

    item_id: str
    item_type: str
    severity: str                      # "info" | "warning" | "critical"
    title: str
    description: str
    related_job_id: str | None
    related_paper_id: str | None
    related_file_id: str | None
    paths: list[str]
    detected_at: datetime
    can_purge: bool
    can_retry_import: bool
    can_manual_promote: bool
    can_restore: bool
    requires_user_input: bool
    risk_level: str                    # "low" | "medium" | "high"
    recommended_actions: list[str]
    safe_actions: list[str]
    dangerous_actions: list[str]
    blockers: list[str]


@dataclass
class MaintenancePreviewResult:
    """Preview of what a maintenance action would do. Never executed."""

    item_id: str
    action: str
    allowed: bool
    risk_level: str                    # "low" | "medium" | "high"
    requires_confirmation: bool
    requires_user_input: bool
    affected_paths: list[str]
    affected_db_records: list[str]
    will_delete_paths: list[str]
    will_update_records: list[str]
    will_create_records: list[str]
    blockers: list[str]
    message: str
