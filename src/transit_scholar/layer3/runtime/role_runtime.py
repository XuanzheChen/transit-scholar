"""Plain-Python Role execution loop."""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from pydantic import BaseModel, ValidationError

from transit_scholar.layer3.agent.models import (
    RoleDefinition,
    RoleExecution,
    RolePolicy,
    RoleResult,
    StructuredOutputRepairContext,
)
from transit_scholar.layer3.agent.registry import RoleRegistry
from transit_scholar.layer3.context import RoleContext


class RoleExecutionStore(Protocol):
    def save(self, execution: RoleExecution) -> None: ...
    def load(self, role_execution_id: str) -> RoleExecution: ...


class ProviderRetryableError(Exception):
    """A transient provider failure eligible for same-step retry."""


class RoleTrace(Protocol):
    def append_event(
        self,
        *,
        agent_run_id: str,
        event_type: str,
        payload: dict[str, Any],
        research_session_id: str | None = None,
    ) -> object: ...


class RoleActionExecutor(Protocol):
    def execute(self, action: object, role: RoleDefinition) -> object: ...


class InMemoryRoleExecutionStore:
    def __init__(self) -> None:
        self._records: dict[str, str] = {}

    def save(self, execution: RoleExecution) -> None:
        self._records[execution.role_execution_id] = execution.model_dump_json()

    def load(self, role_execution_id: str) -> RoleExecution:
        return RoleExecution.model_validate_json(self._records[role_execution_id])


class FileRoleExecutionStore:
    """Durable, atomic JSON snapshots for recoverable Role executions."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def save(self, execution: RoleExecution) -> None:
        target = self._path(execution.role_execution_id)
        self.root.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
        try:
            temporary.write_text(execution.model_dump_json(indent=2) + "\n", encoding="utf-8")
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)

    def load(self, role_execution_id: str) -> RoleExecution:
        return RoleExecution.model_validate_json(self._path(role_execution_id).read_text("utf-8"))

    def _path(self, role_execution_id: str) -> Path:
        if not role_execution_id or Path(role_execution_id).name != role_execution_id:
            raise ValueError("role_execution_id must be a non-empty file-safe identifier")
        return self.root / f"{role_execution_id}.json"


class RoleRuntime:
    """Executes registered one-step and multi-step Roles through one interface."""

    def __init__(
        self,
        registry: RoleRegistry,
        store: RoleExecutionStore | None = None,
        *,
        trace: RoleTrace | None = None,
        action_executor: RoleActionExecutor | None = None,
        is_cancelled: Callable[[], bool] | None = None,
    ) -> None:
        self.registry = registry
        self.store = store or InMemoryRoleExecutionStore()
        self.trace = trace
        self.action_executor = action_executor
        self.is_cancelled = is_cancelled or (lambda: False)

    def execute(
        self,
        role_definition: RoleDefinition,
        role_input: BaseModel | dict[str, object],
        policy: RolePolicy,
        *,
        agent_run_id: str,
        research_session_id: str,
        role_execution_id: str | None = None,
        role_context: RoleContext,
        action_planner: Callable[
            [RoleDefinition, BaseModel, RoleContext], Iterable[object]
        ]
        | None = None,
    ) -> RoleResult:
        if not isinstance(role_context, RoleContext):
            raise TypeError("role_context must be a projected RoleContext")
        if role_context.role_id != role_definition.role_id.value:
            raise ValueError("RoleContext does not match the Role definition")
        registered = self.registry.get(role_definition.role_id)
        if registered != role_definition:
            raise ValueError("Role definition does not match the registered definition")
        validated_input = registered.input_contract.model_validate(role_input)
        execution_id = role_execution_id or uuid4().hex
        execution = self._restore(execution_id) if role_execution_id else None
        if execution is None:
            execution = RoleExecution(
                role_execution_id=execution_id,
                role_id=registered.role_id,
                agent_run_id=agent_run_id,
                research_session_id=research_session_id,
                trace_scope=f"role:{execution_id}",
                runtime_profile=registered.runtime_profile,
            )
            execution.start()
            self._boundary(execution, "role.start")
        else:
            self._validate_recovery(execution, registered, agent_run_id, research_session_id)
            if execution.status != "running":
                return self._result(execution)
            if execution.working_state.operation_in_flight is not None:
                abandoned = execution.working_state.operation_in_flight
                execution.working_state.operation_in_flight = None
                if abandoned.get("kind") == "provider":
                    self._boundary(
                        execution, "role.recovery", classification="provider_call_abandoned"
                    )
                else:
                    execution.working_state.intermediate_artifacts.append(
                        {
                            "action": abandoned.get("action"),
                            "failure": "in_flight_action_abandoned_during_recovery",
                        }
                    )
                    execution.working_state.usage.failures += 1
                    execution.end(
                        status="terminated",
                        reason="in_flight_action_abandoned",
                        failure_message="Recovered an action with unknown commit outcome",
                    )
                    self._boundary(
                        execution, "role.recovery", classification="in_flight_action_abandoned"
                    )
            else:
                self._boundary(execution, "role.recovery", classification="boundary_resumed")

        try:
            while execution.status == "running":
                limit_reason = self._limit_reason(execution)
                if limit_reason:
                    execution.end(status="terminated", reason=limit_reason)
                    break
                output = self._recover_output(execution, registered)
                if output is None:
                    execution.working_state.operation_in_flight = {"kind": "provider"}
                    self._boundary(execution, "role.step", classification="decision_started")
                    output = self._decide(
                        execution, registered, validated_input, policy, role_context
                    )
                    execution.working_state.current_step += 1
                    execution.working_state.last_output = output.model_dump(mode="json")
                    execution.working_state.next_action_index = 0
                    execution.working_state.operation_in_flight = None
                    self._boundary(execution, "role.result", classification="decision_validated")
                actions = tuple(self._actions(output))
                if action_planner is not None:
                    actions += tuple(action_planner(registered, output, role_context))
                for action_index in range(execution.working_state.next_action_index, len(actions)):
                    action = actions[action_index]
                    if self.action_executor is None:
                        raise RuntimeError(
                            "Role output contains actions but no action executor is configured"
                        )
                    if execution.working_state.usage.tool_calls >= execution.runtime_profile.max_tool_calls:
                        execution.end(status="terminated", reason="max_tool_calls")
                        break
                    execution.working_state.next_action_index = action_index + 1
                    execution.working_state.operation_in_flight = {
                        "kind": "tool",
                        "action_index": action_index,
                        "action": self._json_value(action),
                    }
                    self._boundary(execution, "role.action", classification="action_started")
                    action_result = self.action_executor.execute(action, registered)
                    execution.working_state.usage.tool_calls += 1
                    execution.working_state.operation_in_flight = None
                    execution.working_state.intermediate_artifacts.append(
                        {
                            "action": self._json_value(action),
                            "result": self._json_value(action_result),
                        }
                    )
                    self._boundary(execution, "role.action", classification="action_committed")
                if execution.status != "running":
                    break
                if bool(getattr(output, "completed", False)):
                    execution.end(status="completed", reason="semantic_completion")
                    break
                execution.working_state.last_output = None
                execution.working_state.next_action_index = 0
        except _BudgetTermination as exc:
            execution.end(status="terminated", reason=str(exc))
        except Exception as exc:
            execution.working_state.usage.failures += 1
            reason = (
                "max_failures"
                if execution.working_state.usage.failures >= execution.runtime_profile.max_failures
                else "unrecoverable_failure"
            )
            execution.end(status="failed", reason=reason, failure_message=str(exc))

        terminal_event = {
            "completed": "role.completion",
            "failed": "role.failure",
            "terminated": "role.termination",
        }[execution.status]
        self._boundary(execution, terminal_event, classification=execution.termination_reason)
        return self._result(execution)

    def _restore(self, role_execution_id: str) -> RoleExecution | None:
        try:
            return self.store.load(role_execution_id)
        except (KeyError, FileNotFoundError):
            return None

    @staticmethod
    def _validate_recovery(execution, role, agent_run_id, research_session_id):
        if (
            execution.role_id != role.role_id
            or execution.agent_run_id != agent_run_id
            or execution.research_session_id != research_session_id
        ):
            raise ValueError("Persisted Role execution does not match the requested owner or Role")

    @staticmethod
    def _recover_output(execution: RoleExecution, role: RoleDefinition) -> BaseModel | None:
        if execution.working_state.last_output is None:
            return None
        return role.output_contract.model_validate(execution.working_state.last_output)

    @staticmethod
    def _result(execution: RoleExecution) -> RoleResult:
        return RoleResult(
            role_execution_id=execution.role_execution_id,
            role_id=execution.role_id,
            status=execution.status,
            output=execution.working_state.last_output,
            working_state=execution.working_state,
            termination_reason=execution.termination_reason or "unknown",
            failure_message=execution.failure_message,
        )

    def _decide(self, execution, role, role_input, policy, role_context: RoleContext):
        profile = execution.runtime_profile
        provider_retries = 0
        repair_retries = 0
        repair_context = None
        while True:
            if execution.working_state.usage.llm_calls >= profile.max_llm_calls:
                raise _BudgetTermination("max_llm_calls")
            execution.working_state.usage.llm_calls += 1
            try:
                raw_output = policy.decide(
                    role,
                    role_input,
                    execution.working_state,
                    role_context,
                    repair_context,
                )
            except ProviderRetryableError:
                if provider_retries >= profile.provider_retry_limit:
                    raise
                provider_retries += 1
                execution.working_state.retries.provider_retries += 1
                self._boundary(execution, "role.retry", classification="provider_retry")
                continue
            try:
                return role.output_contract.model_validate(raw_output)
            except ValidationError as exc:
                if repair_retries >= profile.structured_output_repair_limit:
                    raise
                repair_retries += 1
                repair_context = StructuredOutputRepairContext(
                    invalid_output=self._json_value(raw_output),
                    validation_errors=tuple(exc.errors(include_url=False)),
                    attempt=repair_retries,
                )
                execution.working_state.retries.structured_output_repairs += 1
                self._boundary(
                    execution, "role.retry", classification="structured_output_repair"
                )

    def _limit_reason(self, execution: RoleExecution) -> str | None:
        state = execution.working_state
        profile = execution.runtime_profile
        if self.is_cancelled():
            return "cancelled"
        if state.current_step >= profile.max_steps:
            return "max_steps"
        if state.usage.llm_calls >= profile.max_llm_calls:
            return "max_llm_calls"
        return None

    @staticmethod
    def _actions(output: BaseModel) -> Iterable[object]:
        actions = getattr(output, "actions", None)
        if actions is not None:
            return actions
        action = getattr(output, "action", None)
        return () if action is None else (action,)

    def _boundary(self, execution: RoleExecution, event_type: str, **payload: Any) -> None:
        self.store.save(execution)
        self._trace(execution, event_type, **payload)

    def _trace(self, execution: RoleExecution, event_type: str, **payload: Any) -> None:
        if self.trace is None:
            return
        self.trace.append_event(
            agent_run_id=execution.agent_run_id,
            research_session_id=execution.research_session_id,
            event_type=event_type,
            payload={
                "role_execution_id": execution.role_execution_id,
                "role_id": execution.role_id.value,
                "step": execution.working_state.current_step,
                "status": execution.status,
                **payload,
            },
        )

    @staticmethod
    def _json_value(value: object) -> Any:
        if isinstance(value, BaseModel):
            return value.model_dump(mode="json")
        if isinstance(value, (str, int, float, bool, list, dict)) or value is None:
            return value
        return repr(value)


class _BudgetTermination(Exception):
    pass


__all__ = [
    "FileRoleExecutionStore",
    "InMemoryRoleExecutionStore",
    "ProviderRetryableError",
    "RoleExecutionStore",
    "RoleRuntime",
]
