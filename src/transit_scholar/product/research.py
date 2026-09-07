"""Thin product service for AgentRun execution and conversation linkage."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from sqlalchemy import select
from transit_scholar.db.models import ConversationTurn

from transit_scholar.layer3.execution import AgentRunService
from .conversation import ConversationGoalResolver, ConversationService


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
        self.execution.update_agent_run_status(agent_run_id, "running")
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
        result = self.execute_run(agent_run_id)
        self._sync_linked_turn(agent_run_id, result)
        return result

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

    def submit_message(self, conversation_id: str, message: str):
        turn = self.conversations.create_turn(conversation_id, message, status="preparing")
        conversation = self.conversations.get_session(conversation_id)
        try:
            prior = self.conversations.recent_completed(conversation_id)
            goal = self.goal_resolver.resolve(message, prior)
            self.conversations.update_turn(turn.id, resolved_user_goal=goal)
            run = self.execution.create_agent_run(workspace_id=conversation.workspace_id, user_goal=goal)
            self.conversations.update_turn(turn.id, agent_run_id=run.agent_run_id, status="running")
            self.session.commit()
            result = self.execute_run(run.agent_run_id, user_goal=goal)
            data = _dump(result)
            artifact = data.get("final_response") if isinstance(data, dict) else None
            run_status = data.get("status") if isinstance(data, dict) else "completed"
            if run_status == "completed":
                self.conversations.update_turn(turn.id, final_assistant_response=_dump(artifact), status="completed", error_message=None)
            else:
                self.conversations.update_turn(turn.id, status="failed", error_message="Research execution failed")
            self.session.commit()
            return self.conversations.get_turn(turn.id)
        except Exception as exc:
            # Keep product-facing failures concise; detailed diagnostics remain
            # in the Core trace/state and the original exception is propagated.
            self.conversations.update_turn(turn.id, status="failed", error_message=str(exc)[:500] or "Research execution failed")
            self.session.commit()
            raise


@dataclass(frozen=True)
class ProductRunState:
    agent_run_id: str
    workspace_id: str
    status: str
    phase: str
    user_goal: str
