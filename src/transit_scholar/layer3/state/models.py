"""Framework-neutral snapshots for recoverable ResearchSession working state."""

from __future__ import annotations

import json
from datetime import datetime
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

if TYPE_CHECKING:  # pragma: no cover - typing only
    from transit_scholar.db.models import ResearchState as ResearchStateRow


class ResearchStateRecord(BaseModel):
    """The latest recoverable working-state payload for one research session."""

    research_session_id: str = Field(min_length=1)
    payload: dict[str, Any]
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_row(cls, row: "ResearchStateRow") -> "ResearchStateRecord":
        return cls(
            research_session_id=row.research_session_id,
            payload=json.loads(row.payload_json),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


__all__ = ["ResearchStateRecord"]
