"""Thin product service for AgentRun execution and conversation linkage."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from sqlalchemy import select
from transit_scholar.db.models import ConversationTurn

from transit_scholar.layer3.execution import AgentRunService
from .conversation import ConversationGoalResolver, ConversationService


class InvalidRunCommand(ValueError):
    """The requested product command is incompatible with the run status."""


@dataclass(frozen=True)
class PreparedMessage:
    """Persisted identities produced before an AgentRun is executed."""

    turn_id: str
    agent_run_id: str
    resolved_user_goal: str


def _dump(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


def _result_data(result: Any) -> dict[str, Any]:
    """Normalize runtime results from dict or pydantic adapters."""
    data = _dump(result)
    if isinstance(data, dict):
        return data
    return {}


class ResearchService:
    def __init__(self, session, runtime_factory, *, conversations=None, goal_resolver=None):
        self.session = session
        self.runtime_factory = runtime_factory
        self.execution = AgentRunService(session)
        self.conversations = conversations or ConversationService(session)
        self.goal_resolver = goal_resolver or ConversationGoalResolver()

    def execute_run(self, agent_run_id: str, *, user_goal: str | None = None):
        run = self.execution.get_agent_run(agent_run_id)
        self._validate_execute_command(run)
        return self._execute(run, agent_run_id, user_goal=user_goal)

    def _execute(self, run, agent_run_id: str, *, user_goal: str | None = None):
        self.execution.update_agent_run_status(agent_run_id, "running")
        self._mark_linked_turn_running(agent_run_id)
        self.session.commit()
        scope = None
        try:
            scope = self.runtime_factory.build_run_scope(agent_run_id)
            runtime = getattr(scope, "run_research_runtime", None) or getattr(scope, "run_runtime", None)
            if runtime is None:
                raise TypeError("run scope does not expose a research runtime")
            result = runtime.execute(
                agent_run_id=agent_run_id, user_goal=user_goal or run.user_goal, agent_run=run
            )
            if hasattr(scope, "close"):
                scope.close()
            scope = None
            data = _result_data(result)
            status = data.get("status")
            if hasattr(status, "value"):
                status = status.value
            if status in {"failed", "terminated", "cancelled"}:
                self.execution.update_agent_run_status(agent_run_id, "failed" if status == "terminated" else status)
            elif status == "completed":
                # Runtime normally owns this transition; retain correctness
                # for lightweight test runtimes and recovery adapters.
                current = self.execution.get_agent_run(agent_run_id)
                if current.status != "completed":
                    self.execution.update_agent_run_status(agent_run_id, "completed")
            self.session.commit()
            # Keep direct/debug execution accessors lifecycle-safe as well:
            # when a run is linked to a turn, project its terminal outcome to
            # that same turn (never create a replacement record).
            self._sync_linked_turn(agent_run_id, result)
            return result
        except Exception:
            self.execution.update_agent_run_status(agent_run_id, "failed")
            self.session.commit()
            self._sync_linked_turn(
                agent_run_id,
                {"status": "failed", "error_message": "Research execution failed"},
            )
            raise
        finally:
            if scope is not None and hasattr(scope, "close"):
                scope.close()

    def resume_run(self, agent_run_id: str):
        run = self.execution.get_agent_run(agent_run_id)
        self._validate_resume_command(run)
        control = getattr(self.runtime_factory, "run_control", None)
        if control is not None:
            control.clear_pause(agent_run_id)
        result = self._execute(run, agent_run_id)
        self._sync_linked_turn(agent_run_id, result)
        return result

    def request_pause(self, agent_run_id: str):
        run = self.execution.get_agent_run(agent_run_id)
        if run.status != "running":
            raise InvalidRunCommand(f"pause_run is only allowed for running runs; current status is {run.status}")
        control = getattr(self.runtime_factory, "run_control", None)
        if control is None:
            raise RuntimeError("runtime factory does not support run control")
        control.request_pause(agent_run_id)
        return run

    @staticmethod
    def _validate_execute_command(run):
        if run.status != "created":
            raise InvalidRunCommand(
                f"execute_run is only allowed for created runs; current status is {run.status}"
            )

    @staticmethod
    def _validate_resume_command(run):
        if run.status != "paused":
            raise InvalidRunCommand(
                f"resume_run is only allowed for paused runs; current status is {run.status}"
            )

    def _sync_linked_turn(self, agent_run_id: str, result):
        turn = self.session.scalar(
            select(ConversationTurn).where(ConversationTurn.agent_run_id == agent_run_id)
        )
        if turn is None:
            return
        data = _dump(result)
        status = data.get("status") if isinstance(data, dict) else None
        if status == "completed":
            artifact = data.get("final_response")
            self.conversations.update_turn(turn.id, final_assistant_response=_dump(artifact), status="completed", error_message=None)
        elif status in {"failed", "terminated", "cancelled"}:
            self.conversations.update_turn(turn.id, status="failed", error_message="Research execution failed")
        self.session.commit()

    def _mark_linked_turn_running(self, agent_run_id: str) -> None:
        turn = self.session.scalar(
            select(ConversationTurn).where(ConversationTurn.agent_run_id == agent_run_id)
        )
        if turn is not None and turn.status == "preparing":
            self.conversations.update_turn(turn.id, status="running")

    def prepare_message(self, conversation_id: str, message: str) -> PreparedMessage:
        """Create and commit a linked Turn and AgentRun without executing it."""
        turn = self.conversations.create_turn(conversation_id, message, status="preparing")
        try:
            conversation = self.conversations.get_session(conversation_id)
            if conversation is None:
                raise ValueError("conversation not found")
            prior = self.conversations.recent_completed(conversation_id)
            goal = self.goal_resolver.resolve(message, prior)
            self.conversations.update_turn(turn.id, resolved_user_goal=goal)
            run = self.execution.create_agent_run(workspace_id=conversation.workspace_id, user_goal=goal)
            self.conversations.update_turn(turn.id, agent_run_id=run.agent_run_id)
            self.session.commit()
            return PreparedMessage(
                turn_id=turn.id,
                agent_run_id=run.agent_run_id,
                resolved_user_goal=goal,
            )
        except Exception as exc:
            # Keep product-facing failures concise; detailed diagnostics remain
            # in the Core trace/state and the original exception is propagated.
            self.conversations.update_turn(turn.id, status="failed", error_message=str(exc)[:500] or "Research execution failed")
            self.session.commit()
            raise

    def submit_message(self, conversation_id: str, message: str):
        """Synchronously prepare, execute, and return the completed Turn."""
        prepared = self.prepare_message(conversation_id, message)
        self.execute_run(
            prepared.agent_run_id,
            user_goal=prepared.resolved_user_goal,
        )
        return self.conversations.get_turn(prepared.turn_id)


@dataclass(frozen=True)
class ProductRunState:
    agent_run_id: str
    workspace_id: str
    status: str
    phase: str
    user_goal: str
    pause_requested: bool = False
    display_status: str | None = None
