"""Thin product service for AgentRun execution and conversation linkage."""
from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
from typing import Any
from sqlalchemy import select
from transit_scholar.db.models import AgentRun, ConversationTurn

from transit_scholar.layer3.execution import AgentRunService
from .conversation import ConversationGoalResolver, ConversationService
from .errors import ProductConflictError, ProductNotFoundError


class InvalidRunCommand(ProductConflictError):
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
    def __init__(self, session, runtime_factory, *, conversations=None, goal_resolver=None, data_root=None):
        self.session = session
        self.runtime_factory = runtime_factory
        self.data_root = data_root
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
            self.session.expire_all()
            current = self.execution.get_agent_run(agent_run_id)
            if current.status in {"completed", "failed", "cancelled"}:
                pass
            elif status in {"failed", "terminated", "cancelled"}:
                self.execution.update_agent_run_status(agent_run_id, "failed" if status == "terminated" else status)
            elif status == "completed":
                # Runtime normally owns this transition; retain correctness
                # for lightweight test runtimes and recovery adapters.
                current = self.execution.get_agent_run(agent_run_id)
                if current.status != "completed":
                    self.execution.update_agent_run_status(agent_run_id, "completed")
            self.session.commit()
        except Exception:
            # A runtime scope may still own an SQL transaction. Release it
            # before recovering the Product session or reading Core truth.
            if scope is not None and hasattr(scope, "close"):
                scope.close()
                scope = None
            self.session.rollback()
            current = self.execution.get_agent_run(agent_run_id)
            if current.status not in {"completed", "failed", "cancelled"}:
                self.execution.update_agent_run_status(agent_run_id, "failed")
                self.session.commit()
                current = self.execution.get_agent_run(agent_run_id)
            if current.status in {"failed", "cancelled"}:
                self._try_sync_linked_turn(agent_run_id, {"status": current.status})
            raise
        finally:
            if scope is not None and hasattr(scope, "close"):
                scope.close()
        # Presentation persistence cannot reclassify the authoritative result.
        self._try_sync_linked_turn(agent_run_id, result)
        return result

    def _try_sync_linked_turn(self, agent_run_id: str, result) -> None:
        try:
            self._sync_linked_turn(agent_run_id, result)
        except Exception:
            self.session.rollback()
            logging.getLogger(__name__).warning(
                "TURN_SYNC_FAILED: deferred conversation projection for run %s", agent_run_id
            )

    def resume_run(self, agent_run_id: str):
        run = self.execution.get_agent_run(agent_run_id)
        self._validate_resume_command(run)
        control = getattr(self.runtime_factory, "run_control", None)
        if control is not None:
            control.clear_pause(agent_run_id)
        return self._execute(run, agent_run_id)

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

    def _load_completed_artifact(self, agent_run_id: str) -> dict:
        """Read durable output without constructing or invoking a runtime."""
        from .runtime import FileRunResearchStateStore
        from transit_scholar.layer3.run_context import RunFinalResponseArtifact
        store = getattr(self.runtime_factory, "state_store", None)
        if store is None:
            root = getattr(self.runtime_factory, "runtime_root", None)
            if root is None:
                from transit_scholar.config import settings
                root = Path(self.data_root or settings.data_root) / "layer3" / "runs"
            store = FileRunResearchStateStore(root)
        if hasattr(store, "load"):
            checkpoint = store.load(agent_run_id)
        elif hasattr(store, "load_state"):
            checkpoint = store.load_state(agent_run_id=agent_run_id)
        else:
            checkpoint = store.get(agent_run_id)
        if not isinstance(checkpoint, dict):
            raise ValueError("missing run checkpoint")
        if checkpoint.get("orchestration_state", {}).get("agent_run_id") != agent_run_id:
            raise ValueError("checkpoint ownership mismatch")
        artifact = RunFinalResponseArtifact.model_validate(checkpoint.get("final_response"))
        if artifact.status != "completed":
            raise ValueError("checkpoint has no completed final response")
        return artifact.model_dump(mode="json")

    def reconcile_interrupted_runs(self, active_run_ids=()) -> list[str]:
        """Startup-only repair; never schedule work or rewrite terminal Runs."""
        active_run_ids = set(active_run_ids)
        reconciled = []
        runs = self.session.scalars(select(AgentRun).where(AgentRun.status == "running")).all()
        for run in runs:
            if run.id not in active_run_ids:
                self.execution.update_agent_run_status(run.id, "paused")
                reconciled.append(run.id)
        orphans = self.session.execute(
            select(AgentRun, ConversationTurn).join(
                ConversationTurn, ConversationTurn.agent_run_id == AgentRun.id
            ).where(AgentRun.status == "created", ConversationTurn.status == "preparing")
        ).all()
        for run, turn in orphans:
            if run.id in active_run_ids:
                continue
            self.execution.update_agent_run_status(run.id, "failed")
            self.conversations.update_turn(
                turn.id, status="failed", error_message="Research admission was interrupted before execution."
            )
            reconciled.append(run.id)
        self.session.commit()
        pending = self.session.execute(
            select(AgentRun, ConversationTurn).join(
                ConversationTurn, ConversationTurn.agent_run_id == AgentRun.id
            ).where(
                AgentRun.status.in_(("completed", "failed", "cancelled")),
                ConversationTurn.status.in_(("preparing", "running")),
            )
        ).all()
        for run, turn in pending:
            if run.id in active_run_ids:
                continue
            if run.status == "completed":
                try:
                    artifact = self._load_completed_artifact(run.id)
                except Exception:
                    self.conversations.update_turn(
                        turn.id, status="failed",
                        error_message="TURN_RECOVERY_FAILED: Durable final response is unavailable.",
                    )
                else:
                    self.conversations.update_turn(
                        turn.id, status="completed", final_assistant_response=artifact, error_message=None,
                    )
            else:
                self.conversations.update_turn(turn.id, status="failed", error_message="Research execution failed")
            reconciled.append(run.id)
        self.session.commit()
        return reconciled

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
                raise ProductNotFoundError("conversation not found")
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
            self.conversations.update_turn(turn.id, status="failed", error_message="Research execution failed")
            self.session.commit()
            raise

    def discard_prepared_message(self, prepared: PreparedMessage) -> None:
        """Remove persisted preparation state when local scheduling fails."""
        turn = self.conversations.get_turn(prepared.turn_id)
        if turn is not None and turn.agent_run_id == prepared.agent_run_id:
            self.session.delete(turn)
        run = self.session.get(AgentRun, prepared.agent_run_id)
        if run is not None and run.status == "created":
            self.session.delete(run)
        self.session.commit()

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
