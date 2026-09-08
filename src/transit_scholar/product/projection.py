"""Read-only product state projections."""
from __future__ import annotations

import json
from copy import deepcopy
from sqlalchemy import select

from transit_scholar.db.models import AgentTraceEvent, EvidenceRecord, Paper, ResearchSession
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
        control = getattr(self.runtime_factory, "run_control", None)
        pause_requested = (
            run.status == "running"
            and control is not None
            and control.is_pause_requested(run.agent_run_id)
        )
        return ProductRunState(
            run.agent_run_id, run.workspace_id, run.status, phase, run.user_goal,
            pause_requested=pause_requested,
            display_status="pause_requested" if pause_requested else run.status,
        )

    get_run_state = run_state
    project_run_state = run_state

    def run_timeline(self, agent_run_id: str, after_sequence: int = 0) -> list[dict]:
        """Project durable trace events into a UI-safe incremental timeline."""
        from transit_scholar.layer3.execution import AgentRunService
        if after_sequence < 0:
            raise ValueError("after_sequence must be non-negative")
        AgentRunService(self.session).get_agent_run(agent_run_id)
        rows = self.session.execute(
            select(AgentTraceEvent)
            .where(
                AgentTraceEvent.agent_run_id == agent_run_id,
                AgentTraceEvent.sequence > after_sequence,
            )
            .order_by(AgentTraceEvent.sequence)
        ).scalars().all()
        kind_map = {
            "runtime.start": "status", "runtime.resume": "status",
            "runtime.step": "status", "runtime.completion": "status",
            "runtime.failure": "error", "runtime.action": "retrieval",
            "run.plan.created": "planning", "run.plan.updated": "planning",
            "run.replan": "planning", "run.session.created": "research_session",
            "run.session.started": "research_session",
        }
        safe_keys = {
            "role_id", "role_status", "action_type", "status", "termination_reason",
            "query_id", "evidence_id", "claim_id", "research_session_id",
            "message", "warning", "error", "failure_message",
        }
        result = []
        for row in rows:
            try:
                payload = json.loads(row.payload_json or "{}")
            except (TypeError, ValueError):
                payload = {}
            if not isinstance(payload, dict):
                payload = {}
            data = {
                key: value
                for key in safe_keys
                if key in payload and (value := _timeline_value(payload[key])) is not None
            }
            kind = kind_map.get(row.event_type)
            if kind is None:
                lowered = row.event_type.lower()
                kind = next((candidate for token, candidate in (
                    ("plan", "planning"), ("session", "research_session"),
                    ("query", "query"), ("retriev", "retrieval"),
                    ("evidence", "evidence"), ("claim", "claim"),
                    ("synth", "synthesis"), ("warn", "warning"),
                    ("error", "error"),
                ) if token in lowered), "status")
            result.append({
                "sequence": row.sequence,
                "kind": kind,
                "timestamp": row.created_at,
                "research_session_id": row.research_session_id,
                "data": data,
            })
        return result

    timeline = run_timeline

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
            response = deepcopy(turn.final_assistant_response)
            turns.append({
                "turn_id": turn.id,
                "sequence": turn.sequence,
                "user_message": turn.user_message,
                "resolved_user_goal": turn.resolved_user_goal,
                "assistant_response": response,
                "final_answer": _final_answer(response),
                "answer_citations": self.answer_citations(turn.agent_run_id, response),
                "agent_run_id": turn.agent_run_id,
                "status": turn.status,
                "error_message": turn.error_message,
                "run_state": state,
            })
        return {"conversation_id": conversation.id, "workspace_id": conversation.workspace_id, "title": conversation.title, "turns": turns}

    # Explicit aliases make the projection boundary discoverable without
    # introducing another persistence or service abstraction.
    conversation_view = conversation
    read_conversation = conversation

    def answer_citations(self, agent_run_id: str | None, final_response: object) -> list[dict]:
        """Resolve final-artifact evidence IDs to persisted admitted evidence.

        Citation order follows the final response.  No provenance is derived
        from a model response alone: an ID must resolve to evidence admitted
        under the same AgentRun before it is exposed.
        """
        citation_ids = _citation_ids(final_response)
        if not agent_run_id or not citation_ids:
            return []
        rows = self.session.execute(
            select(EvidenceRecord)
            .join(ResearchSession, EvidenceRecord.research_session_id == ResearchSession.id)
            .where(
                ResearchSession.agent_run_id == agent_run_id,
                EvidenceRecord.id.in_(citation_ids),
            )
        ).scalars().all()
        evidence_by_id = {row.id: row for row in rows}
        paper_ids = {
            locator.get("paper_id")
            for row in rows
            if (locator := _json_object(row.locator_json)).get("paper_id")
        }
        titles = {
            row.id: row.title
            for row in self.session.execute(select(Paper).where(Paper.id.in_(paper_ids))).scalars()
        } if paper_ids else {}
        citations = []
        for evidence_id in citation_ids:
            row = evidence_by_id.get(evidence_id)
            if row is None:
                continue
            locator = _json_object(row.locator_json)
            metadata = _json_object(row.source_metadata_json)
            paper_provenance = metadata.get("paper_provenance")
            paper_provenance = paper_provenance if isinstance(paper_provenance, dict) else {}
            span = locator.get("span")
            span = span if isinstance(span, dict) else {}
            paper_id = locator.get("paper_id")
            citations.append({
                "evidence_id": row.id,
                "research_session_id": row.research_session_id,
                "paper_id": paper_id,
                "paper_title": paper_provenance.get("title") or titles.get(paper_id),
                "source_kind": locator.get("source_kind") or metadata.get("source_kind") or "unknown",
                "pages": locator.get("pages"),
                "block_id": locator.get("block_id"),
                "character_start": span.get("start"),
                "character_end": span.get("end"),
                "parse_run_id": locator.get("parse_run_id"),
                "canonical_source_version": locator.get("canonical_source_version"),
                "evidence_quote": row.text_snapshot,
            })
        return citations

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


def _timeline_value(value):
    """Retain only simple presentation values from a trace payload.

    Trace payloads are runtime diagnostics, so even fields selected for the
    public projection must not carry nested prompt, scratchpad, or provider
    response structures through the API boundary.
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        return deepcopy(value)
    if isinstance(value, list) and all(item is None or isinstance(item, (str, int, float, bool)) for item in value):
        return deepcopy(value)
    return None


def _json_object(value: object) -> dict:
    try:
        loaded = json.loads(value) if isinstance(value, str) else value
    except (TypeError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _citation_ids(final_response: object) -> list[str]:
    response = final_response if isinstance(final_response, dict) else {}
    identifiers = response.get("citation_references")
    if not isinstance(identifiers, list):
        return []
    return list(dict.fromkeys(identifier for identifier in identifiers if isinstance(identifier, str) and identifier))


def _final_answer(final_response: object) -> str | None:
    response = final_response if isinstance(final_response, dict) else {}
    for key in ("answer_text", "answer"):
        value = response.get(key)
        if isinstance(value, str):
            return value
    return None
