"""Durable, framework-neutral lifecycle services for AgentRun and sessions."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from transit_scholar.db.models import AGENT_EXECUTION_STATUSES, AgentRun, ResearchSession
from transit_scholar.layer3.workspace import WorkspaceService

from .errors import (
    AgentRunNotFoundError,
    InvalidExecutionInputError,
    ResearchSessionNotFoundError,
    ResearchSessionOwnershipError,
)
from .models import AgentRunRecord, ResearchSessionRecord


def _new_id() -> str:
    return uuid.uuid4().hex


def _non_empty(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidExecutionInputError(f"{label} must be a non-empty string")
    return value.strip()


def _status(value: str) -> str:
    value = _non_empty(value, "status")
    if value not in AGENT_EXECUTION_STATUSES:
        raise InvalidExecutionInputError(
            f"status must be one of {AGENT_EXECUTION_STATUSES!r}"
        )
    return value


class AgentRunService:
    """Authoritative persistence API for runs and their owned sessions.

    This service intentionally has no dependency on an agent-loop framework or
    on a planning artifact. It is a lifecycle/control-plane boundary only.
    """

    def __init__(self, session: Session) -> None:
        self.session = session
        self.workspaces = WorkspaceService(session)

    def create_agent_run(
        self,
        *,
        workspace_id: str,
        user_goal: str,
        status: str = "created",
        agent_run_id: str | None = None,
    ) -> AgentRunRecord:
        """Create a run after validating the current active Workspace."""
        workspace = self.workspaces.require_active(workspace_id)
        row = AgentRun(
            id=_non_empty(agent_run_id, "agent_run_id") if agent_run_id else _new_id(),
            workspace_id=workspace.workspace_id,
            user_goal=_non_empty(user_goal, "user_goal"),
            status=_status(status),
            workspace_revision=workspace.revision,
        )
        self.session.add(row)
        self.session.flush()
        self.session.refresh(row)
        return AgentRunRecord.from_row(row)

    def get_agent_run(self, agent_run_id: str) -> AgentRunRecord:
        return AgentRunRecord.from_row(self._get_run(agent_run_id))

    read_agent_run = get_agent_run

    def update_agent_run_status(self, agent_run_id: str, status: str) -> AgentRunRecord:
        row = self._get_run(agent_run_id)
        row.status = _status(status)
        self.session.flush()
        self.session.refresh(row)
        return AgentRunRecord.from_row(row)

    def create_research_session(
        self,
        *,
        agent_run_id: str,
        research_question: str,
        status: str = "created",
        research_session_id: str | None = None,
    ) -> ResearchSessionRecord:
        run = self._get_run(agent_run_id)
        row = ResearchSession(
            id=(
                _non_empty(research_session_id, "research_session_id")
                if research_session_id
                else _new_id()
            ),
            agent_run_id=run.id,
            research_question=_non_empty(research_question, "research_question"),
            status=_status(status),
        )
        self.session.add(row)
        self.session.flush()
        self.session.refresh(row)
        return ResearchSessionRecord.from_row(row)

    def list_research_sessions(self, agent_run_id: str) -> list[ResearchSessionRecord]:
        run = self._get_run(agent_run_id)
        rows = self.session.execute(
            select(ResearchSession)
            .where(ResearchSession.agent_run_id == run.id)
            .order_by(ResearchSession.created_at, ResearchSession.id)
        ).scalars().all()
        return [ResearchSessionRecord.from_row(row) for row in rows]

    def get_research_session(
        self, agent_run_id: str, research_session_id: str
    ) -> ResearchSessionRecord:
        return ResearchSessionRecord.from_row(
            self._get_owned_session(agent_run_id, research_session_id)
        )

    read_research_session = get_research_session

    def update_research_session_status(
        self, agent_run_id: str, research_session_id: str, status: str
    ) -> ResearchSessionRecord:
        row = self._get_owned_session(agent_run_id, research_session_id)
        row.status = _status(status)
        self.session.flush()
        self.session.refresh(row)
        return ResearchSessionRecord.from_row(row)

    def _get_run(self, agent_run_id: str) -> AgentRun:
        agent_run_id = _non_empty(agent_run_id, "agent_run_id")
        row = self.session.get(AgentRun, agent_run_id)
        if row is None:
            raise AgentRunNotFoundError(f"agent run {agent_run_id!r} does not exist")
        return row

    def _get_owned_session(
        self, agent_run_id: str, research_session_id: str
    ) -> ResearchSession:
        run = self._get_run(agent_run_id)
        research_session_id = _non_empty(research_session_id, "research_session_id")
        row = self.session.get(ResearchSession, research_session_id)
        if row is None:
            raise ResearchSessionNotFoundError(
                f"research session {research_session_id!r} does not exist"
            )
        if row.agent_run_id != run.id:
            raise ResearchSessionOwnershipError(
                f"research session {research_session_id!r} is not owned by agent run {run.id!r}"
            )
        return row


ExecutionService = AgentRunService
ResearchSessionService = AgentRunService

__all__ = ["AgentRunService", "ResearchSessionService", "ExecutionService"]
