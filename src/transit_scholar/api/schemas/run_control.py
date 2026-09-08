from datetime import datetime
from typing import Any, Literal
from pydantic import BaseModel, Field


class RunStateResponse(BaseModel):
    agent_run_id: str
    workspace_id: str
    status: str
    phase: str
    user_goal: str
    pause_requested: bool = False
    display_status: str | None = None


class TimelineEventResponse(BaseModel):
    sequence: int = Field(ge=1)
    kind: Literal[
        "planning", "research_session", "query", "retrieval", "evidence",
        "claim", "synthesis", "status", "warning", "error",
    ]
    timestamp: datetime
    research_session_id: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)


class TimelineResponse(BaseModel):
    events: list[TimelineEventResponse]
    next_sequence: int = Field(ge=0)
