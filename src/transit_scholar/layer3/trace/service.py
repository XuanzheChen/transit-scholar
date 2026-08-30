"""Append-oriented, framework-neutral persistence for Agent execution traces."""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from transit_scholar.db.models import AgentRun, AgentTraceEvent
from transit_scholar.layer3.execution import AgentRunService, InvalidExecutionInputError

from .models import AgentTraceEventRecord


def _new_id() -> str:
    return uuid.uuid4().hex


def _non_empty(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidExecutionInputError(f"{label} must be a non-empty string")
    return value.strip()


def _serialize_payload(payload: Any) -> str:
    if not isinstance(payload, dict):
        raise InvalidExecutionInputError("payload must be a structured mapping")
    try:
        serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        restored = json.loads(serialized)
    except (TypeError, ValueError) as exc:
        raise InvalidExecutionInputError(
            "payload must be JSON-serializable structured data"
        ) from exc
    if restored != payload:
        raise InvalidExecutionInputError(
            "payload must use JSON-compatible mapping keys and values"
        )
    return serialized


class AgentTraceService:
    """Append and read execution events without imposing runtime semantics."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.execution = AgentRunService(session)

    def append_event(
        self,
        *,
        agent_run_id: str,
        event_type: str,
        payload: dict[str, Any],
        research_session_id: str | None = None,
        event_id: str | None = None,
    ) -> AgentTraceEventRecord:
        """Append one event, assigning its next stable per-run sequence number."""
        run = self.execution._get_run(agent_run_id)
        if research_session_id is not None:
            self.execution.get_research_session(run.id, research_session_id)

        next_sequence = self.session.execute(
            update(AgentRun)
            .where(AgentRun.id == run.id)
            .values(trace_sequence=AgentRun.trace_sequence + 1)
            .returning(AgentRun.trace_sequence)
        ).scalar_one()
        row = AgentTraceEvent(
            id=_non_empty(event_id, "event_id") if event_id else _new_id(),
            agent_run_id=run.id,
            research_session_id=research_session_id,
            sequence=next_sequence,
            event_type=_non_empty(event_type, "event_type"),
            payload_json=_serialize_payload(payload),
        )
        self.session.add(row)
        self.session.flush()
        self.session.refresh(row)
        return AgentTraceEventRecord.from_row(row)

    append_trace_event = append_event

    def read_trace(
        self, *, agent_run_id: str, research_session_id: str | None = None
    ) -> list[AgentTraceEventRecord]:
        """Return a full run trace or exactly one owned session's events."""
        run = self.execution._get_run(agent_run_id)
        query = select(AgentTraceEvent).where(AgentTraceEvent.agent_run_id == run.id)
        if research_session_id is not None:
            self.execution.get_research_session(run.id, research_session_id)
            query = query.where(AgentTraceEvent.research_session_id == research_session_id)
        rows = self.session.execute(query.order_by(AgentTraceEvent.sequence)).scalars().all()
        return [AgentTraceEventRecord.from_row(row) for row in rows]

    get_trace = read_trace
    read_agent_trace = read_trace


__all__ = ["AgentTraceService"]
