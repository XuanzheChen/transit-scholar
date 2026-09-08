"""Persistence and context helpers for product conversations."""
from __future__ import annotations

from datetime import datetime, timezone
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from pydantic import BaseModel, ConfigDict, Field

from transit_scholar.db.models import ConversationSession, ConversationTurn
from transit_scholar.layer3.workspace import WorkspaceService
from transit_scholar.layer3.workspace.errors import WorkspaceError, WorkspaceNotFoundError
from .errors import ProductConflictError, ProductNotFoundError, ProductValidationError


class ConversationService:
    def __init__(self, session: Session, recent_limit: int = 6):
        self.session = session
        self.recent_limit = recent_limit
        self.workspaces = WorkspaceService(session)

    def create_session(self, workspace_id: str, title: str | None = None) -> ConversationSession:
        try:
            self.workspaces.require_active(workspace_id)
        except WorkspaceNotFoundError as exc:
            raise ProductNotFoundError("workspace not found") from exc
        except WorkspaceError:
            raise ProductConflictError("conversation requires an active workspace")
        row = ConversationSession(workspace_id=workspace_id, title=title)
        self.session.add(row)
        self.session.flush()
        return row

    def get_session(self, conversation_id: str) -> ConversationSession | None:
        return self.session.get(ConversationSession, conversation_id)

    def list_sessions(self, workspace_id: str) -> list[ConversationSession]:
        try:
            self.workspaces.get(workspace_id)
        except WorkspaceNotFoundError as exc:
            raise ProductNotFoundError("workspace not found") from exc
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
            raise ProductNotFoundError("conversation not found")
        if not isinstance(user_message, str) or not user_message.strip():
            raise ProductValidationError("user message must not be empty")
        if "status" in values and values["status"] not in {"preparing", "running", "completed", "failed"}:
            raise ProductValidationError("invalid conversation turn status")
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
            raise ProductNotFoundError("turn not found")
        for key, value in values.items():
            if key not in {"resolved_user_goal", "agent_run_id", "final_assistant_response", "status", "error_message", "completed_at"}:
                raise ProductValidationError(f"unsupported turn field: {key}")
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
            raise ProductValidationError("user message must not be empty")
        prior_turns = prior_turns or []
        if not prior_turns:
            return message
        if self.generator is None:
            raise ProductValidationError("goal generator is required when prior conversation turns are provided")
        generated = self.generator(message, prior_turns)
        if isinstance(generated, dict):
            generated = generated.get("resolved_user_goal")
        elif hasattr(generated, "resolved_user_goal"):
            generated = generated.resolved_user_goal
        if not isinstance(generated, str) or not generated.strip():
            raise ProductValidationError("goal generator must return a non-empty resolved_user_goal")
        return generated.strip()

    resolve_goal = resolve

    def __call__(self, user_message: str, prior_turns: list[ConversationTurn] | None = None) -> str:
        return self.resolve(user_message, prior_turns)


class ConversationGoalOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resolved_user_goal: str = Field(min_length=1)
