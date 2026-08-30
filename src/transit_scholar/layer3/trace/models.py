"""Framework-neutral AgentTrace event snapshots."""

from __future__ import annotations

import json
from datetime import datetime
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

if TYPE_CHECKING:  # pragma: no cover - typing only
    from transit_scholar.db.models import AgentTraceEvent as AgentTraceEventRow


class AgentTraceEventRecord(BaseModel):
    """One durable execution event in AgentRun sequence order."""

    event_id: str = Field(min_length=1)
    agent_run_id: str = Field(min_length=1)
    research_session_id: str | None = None
    sequence: int = Field(ge=1)
    event_type: str = Field(min_length=1)
    payload: dict[str, Any]
    timestamp: datetime

    @classmethod
    def from_row(cls, row: "AgentTraceEventRow") -> "AgentTraceEventRecord":
        return cls(
            event_id=row.id,
            agent_run_id=row.agent_run_id,
            research_session_id=row.research_session_id,
            sequence=row.sequence,
            event_type=row.event_type,
            payload=json.loads(row.payload_json),
            timestamp=row.created_at,
        )


AgentTraceEvent = AgentTraceEventRecord

__all__ = ["AgentTraceEvent", "AgentTraceEventRecord"]
