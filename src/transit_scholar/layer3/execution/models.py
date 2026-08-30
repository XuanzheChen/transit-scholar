"""Framework-neutral snapshots for Layer3 Stage2 execution persistence."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

if TYPE_CHECKING:  # pragma: no cover - typing only
    from transit_scholar.db.models import AgentRun as AgentRunRow
    from transit_scholar.db.models import ResearchSession as ResearchSessionRow


ExecutionStatus = Literal[
    "created", "running", "paused", "completed", "failed", "cancelled"
]


class AgentRunRecord(BaseModel):
    """Immutable read snapshot of one complete user-initiated Agent task."""

    agent_run_id: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    user_goal: str = Field(min_length=1)
    status: ExecutionStatus
    workspace_revision: int = Field(ge=1)
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_row(cls, row: "AgentRunRow") -> "AgentRunRecord":
        return cls(
            agent_run_id=row.id,
            workspace_id=row.workspace_id,
            user_goal=row.user_goal,
            status=row.status,
            workspace_revision=row.workspace_revision,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


class ResearchSessionRecord(BaseModel):
    """Immutable read snapshot of one AgentRun-owned research question."""

    research_session_id: str = Field(min_length=1)
    agent_run_id: str = Field(min_length=1)
    research_question: str = Field(min_length=1)
    status: ExecutionStatus
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_row(cls, row: "ResearchSessionRow") -> "ResearchSessionRecord":
        return cls(
            research_session_id=row.id,
            agent_run_id=row.agent_run_id,
            research_question=row.research_question,
            status=row.status,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


__all__ = ["AgentRunRecord", "ResearchSessionRecord", "ExecutionStatus"]
