"""Read-only product state projections."""
from __future__ import annotations

import json
from copy import deepcopy
from sqlalchemy import select

from transit_scholar.db.models import AgentTraceEvent, ResearchSession
from .research import ProductRunState


class ProductStateProjector:
    def __init__(self, session, runtime_factory):
        self.session, self.runtime_factory = session, runtime_factory

    def run_state(self, agent_run_id: str) -> ProductRunState:
        from transit_scholar.layer3.execution import AgentRunService
        # Runtime execution uses a disposable session; discard any identity-map
        # snapshot held by the facade session before projecting authoritative
        # Core state.
        self.session.expire_all()
        run = AgentRunService(self.session).get_agent_run(agent_run_id)
        phase = self._derive_phase(run)
        return ProductRunState(run.agent_run_id, run.workspace_id, run.status, phase, run.user_goal)

    get_run_state = run_state
    project_run_state = run_state

    def conversation(self, conversation_id: str):
        from .conversation import ConversationService
        conversation = ConversationService(self.session).get_session(conversation_id)
        if conversation is None:
            raise ValueError("conversation not found")
        turns = []
        for turn in ConversationService(self.session).list_turns(conversation_id):
            # A conversation is product-owned history.  A stale/missing link
            # must not make the display projection mutate state or fail to load.
            state = None
            if turn.agent_run_id:
                try:
                    state = self.run_state(turn.agent_run_id)
                except Exception:
                    state = None
            turns.append({"turn_id": turn.id, "sequence": turn.sequence, "user_message": turn.user_message, "resolved_user_goal": turn.resolved_user_goal, "assistant_response": deepcopy(turn.final_assistant_response), "agent_run_id": turn.agent_run_id, "status": turn.status, "error_message": turn.error_message, "run_state": state})
        return {"conversation_id": conversation.id, "workspace_id": conversation.workspace_id, "title": conversation.title, "turns": turns}

    # Explicit aliases make the projection boundary discoverable without
    # introducing another persistence or service abstraction.
    conversation_view = conversation
    read_conversation = conversation

    def _derive_phase(self, run) -> str:
        """Derive a presentation phase from Core records only.

        No phase is persisted or written back. Terminal AgentRun status wins;
        otherwise the latest trace event and owned session statuses provide the
        most useful display-level signal, with conservative fallbacks.
        """
        terminal = {"completed": "completed", "failed": "failed", "cancelled": "failed"}
        if run.status in terminal:
            return terminal[run.status]

        events = self.session.execute(
            select(AgentTraceEvent)
            .where(AgentTraceEvent.agent_run_id == run.agent_run_id)
            .order_by(AgentTraceEvent.sequence.desc())
            .limit(1)
        ).scalars().all()
        if events:
            event = events[0]
            payload = {}
            try:
                payload = json.loads(event.payload_json or "{}")
            except (TypeError, ValueError):
                payload = {}
            event_type = event.event_type
            role = payload.get("role_id")
            role_phases = {
                "research_coordinator": "planning",
                "query_planning": "query_planning",
                "evidence_reasoning": "evidence_reasoning",
                "claim_reasoning": "claim_reasoning",
                "final_synthesis": "synthesis",
            }
            if role in role_phases and event_type.startswith("role."):
                return role_phases[role]
            event_phases = {
                "run.synthesis.started": "synthesis",
                "run.plan.created": "planning",
                "run.plan.updated": "planning",
                "run.replan": "planning",
                "run.session.created": "planning",
                "run.session.started": "query_planning",
            }
            if event_type in event_phases:
                return event_phases[event_type]

        sessions = self.session.execute(
            select(ResearchSession.status)
            .where(ResearchSession.agent_run_id == run.agent_run_id)
            .order_by(ResearchSession.updated_at.desc(), ResearchSession.id.desc())
            .limit(1)
        ).scalars().all()
        if sessions and sessions[0] in {"failed", "cancelled"}:
            return "failed"
        if sessions and sessions[0] == "completed":
            return "synthesis"
        return "planning" if run.status == "created" else "research"
