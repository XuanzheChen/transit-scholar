"""Persistence and context helpers for product conversations."""
from __future__ import annotations

from datetime import datetime, timezone
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from transit_scholar.db.models import ConversationSession, ConversationTurn
from transit_scholar.layer3.workspace import WorkspaceService
from transit_scholar.layer3.workspace.errors import WorkspaceError


class ConversationService:
    def __init__(self, session: Session, recent_limit: int = 6):
        self.session = session
        self.recent_limit = recent_limit
        self.workspaces = WorkspaceService(session)

    def create_session(self, workspace_id: str, title: str | None = None) -> ConversationSession:
        try:
            self.workspaces.require_active(workspace_id)
        except WorkspaceError:
            raise ValueError("conversation requires an active workspace")
        row = ConversationSession(workspace_id=workspace_id, title=title)
        self.session.add(row)
        self.session.flush()
        return row

    def get_session(self, conversation_id: str) -> ConversationSession | None:
        return self.session.get(ConversationSession, conversation_id)

    def list_sessions(self, workspace_id: str) -> list[ConversationSession]:
        return list(self.session.scalars(
            select(ConversationSession)
            .where(ConversationSession.workspace_id == workspace_id)
            .order_by(ConversationSession.created_at, ConversationSession.id)
        ).all())

    create_conversation = create_session
    get_conversation = get_session
    list_conversations = list_sessions

    def create_turn(self, conversation_id: str, user_message: str, **values) -> ConversationTurn:
        conversation = self.session.get(ConversationSession, conversation_id)
        if conversation is None:
            raise ValueError("conversation not found")
        if not isinstance(user_message, str) or not user_message.strip():
            raise ValueError("user message must not be empty")
        if "status" in values and values["status"] not in {"preparing", "running", "completed", "failed"}:
            raise ValueError("invalid conversation turn status")
        next_sequence = self.session.scalar(
            select(func.coalesce(func.max(ConversationTurn.sequence), 0) + 1).where(
                ConversationTurn.conversation_id == conversation_id
            )
        )
        row = ConversationTurn(conversation_id=conversation_id, sequence=int(next_sequence), user_message=user_message, **values)
        self.session.add(row)
        self.session.flush()
        return row

    def get_turn(self, turn_id: str) -> ConversationTurn | None:
        return self.session.get(ConversationTurn, turn_id)

    def list_turns(self, conversation_id: str) -> list[ConversationTurn]:
        return list(self.session.scalars(
            select(ConversationTurn)
            .where(ConversationTurn.conversation_id == conversation_id)
            .order_by(ConversationTurn.sequence)
        ).all())

    read_turns = list_turns

    def update_turn(self, turn_id: str, **values) -> ConversationTurn:
        row = self.session.get(ConversationTurn, turn_id)
        if row is None:
            raise ValueError("turn not found")
        for key, value in values.items():
            if key not in {"resolved_user_goal", "agent_run_id", "final_assistant_response", "status", "error_message", "completed_at"}:
                raise ValueError(f"unsupported turn field: {key}")
            setattr(row, key, value)
        if row.status == "completed" and row.completed_at is None:
            row.completed_at = datetime.now(timezone.utc)
        self.session.flush()
        return row

    def recent_completed(self, conversation_id: str, limit: int | None = None) -> list[ConversationTurn]:
        count = self.recent_limit if limit is None else max(0, limit)
        rows = self.session.scalars(
            select(ConversationTurn)
            .where(ConversationTurn.conversation_id == conversation_id, ConversationTurn.status == "completed")
            .order_by(ConversationTurn.sequence.desc()).limit(count)
        ).all()
        return list(reversed(rows))


class ConversationGoalResolver:
    """Resolve dialogue references without creating any core research state."""

    def __init__(self, generator=None):
        """Optionally accept a structured goal-generation callable.

        The callable receives ``current_message`` and ``prior_turns`` and may
        return either a goal string or a mapping containing ``resolved_user_goal``.
        """
        self.generator = generator

    def resolve(self, user_message: str, prior_turns: list[ConversationTurn] | None = None) -> str:
        message = user_message.strip()
        if not message:
            raise ValueError("user message must not be empty")
        prior_turns = prior_turns or []
        if self.generator is not None:
            generated = self.generator(message, prior_turns)
            if isinstance(generated, dict):
                generated = generated.get("resolved_user_goal")
            elif hasattr(generated, "resolved_user_goal"):
                generated = generated.resolved_user_goal
            if isinstance(generated, str) and generated.strip():
                return generated.strip()
        if not prior_turns:
            return message
        context = "\n".join(
            f"Earlier user request: {turn.user_message}\n"
            f"Earlier resolved goal: {turn.resolved_user_goal or ''}\n"
            f"Earlier assistant response: {turn.final_assistant_response or ''}"
            for turn in prior_turns
        )
        import re
        lowered = message.casefold()
        referent = None
        if "that one" in lowered or "that paper" in lowered or "the second paper" in lowered:
            latest = prior_turns[-1]
            response = latest.final_assistant_response
            papers = response.get("papers") if isinstance(response, dict) else None
            if isinstance(papers, list) and papers:
                index = 1 if ("second" in lowered or "second" in (latest.resolved_user_goal or "").casefold()) else -1
                if len(papers) > abs(index):
                    referent = str(papers[index])
            if referent is None:
                match = re.search(r"(?:second|2nd)\s+paper", latest.resolved_user_goal or "", re.I)
                if match:
                    referent = "the second paper identified in the prior conversation"
        if referent:
            normalized = re.sub(r"\b(?:that one|that paper)\b", referent, message, flags=re.I)
            prior_goal = (prior_turns[-1].resolved_user_goal or "").strip()
            qualifier = f" Prior context established: {prior_goal}." if prior_goal else ""
            return f"{normalized}.{qualifier}" if not normalized.endswith((".", "!", "?")) else f"{normalized}{qualifier}"
        return f"{message} (use the relevant referent and constraints established in the prior conversation)\nPrior context:\n{context}"

    resolve_goal = resolve

    def __call__(self, user_message: str, prior_turns: list[ConversationTurn] | None = None) -> str:
        return self.resolve(user_message, prior_turns)
