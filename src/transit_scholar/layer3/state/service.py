"""Durable state persistence for framework-neutral ResearchSession recovery."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from transit_scholar.db.models import ResearchState
from transit_scholar.layer3.execution import AgentRunService, InvalidExecutionInputError

from .models import ResearchStateRecord


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


class ResearchStateService:
    """Read and replace the latest state owned by an AgentRun session boundary."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.execution = AgentRunService(session)

    def save_research_state(
        self,
        *,
        agent_run_id: str,
        research_session_id: str,
        payload: dict[str, Any],
    ) -> ResearchStateRecord:
        """Persist the latest framework-neutral state for an owned session."""
        owned_session = self.execution.get_research_session(
            agent_run_id, research_session_id
        )
        serialized = _serialize_payload(payload)
        row = self.session.get(ResearchState, owned_session.research_session_id)
        if row is None:
            row = ResearchState(
                research_session_id=owned_session.research_session_id,
                payload_json=serialized,
            )
            self.session.add(row)
        else:
            row.payload_json = serialized
        self.session.flush()
        self.session.refresh(row)
        return ResearchStateRecord.from_row(row)

    save_state = save_research_state

    def load_research_state(
        self, *, agent_run_id: str, research_session_id: str
    ) -> ResearchStateRecord | None:
        """Load the owned session's latest state, if it was previously saved."""
        owned_session = self.execution.get_research_session(
            agent_run_id, research_session_id
        )
        row = self.session.get(ResearchState, owned_session.research_session_id)
        return ResearchStateRecord.from_row(row) if row is not None else None

    load_state = load_research_state
    get_research_state = load_research_state


__all__ = ["ResearchStateService"]
