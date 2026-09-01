"""Plain-Python orchestration loop for one ResearchSession."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from transit_scholar.layer3.actions.models import AgentAction
from transit_scholar.layer3.agent import (
    FinalResponseArtifact,
    FinalSynthesisRole,
    RoleDefinition,
    RoleId,
    RolePolicy,
    RoleRegistry,
    RoleResult,
)
from transit_scholar.layer3.context import RoleContextProjector

from .config import MainRuntimeConfig
from .role_runtime import RoleRuntime


class MainRuntimeUsage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    steps: int = Field(default=0, ge=0)
    llm_calls: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    failures: int = Field(default=0, ge=0)


class MainRuntimeResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_run_id: str
    research_session_id: str
    status: str
    termination_reason: str
    usage: MainRuntimeUsage
    role_results: list[RoleResult] = Field(default_factory=list)
    final_response: FinalResponseArtifact | None = None
    failure_message: str | None = None


class ContextBuilder(Protocol):
    def build(self, *, agent_run_id: str, research_session_id: str, **kwargs: Any) -> object: ...


class ExecutionService(Protocol):
    def get_agent_run(self, agent_run_id: str) -> object: ...
    def get_research_session(self, agent_run_id: str, research_session_id: str) -> object: ...
    def update_research_session_status(
        self, agent_run_id: str, research_session_id: str, status: str
    ) -> object: ...


class TraceSink(Protocol):
    def append_event(self, **event: Any) -> object: ...


RoleInputFactory = Callable[[RoleId, object], BaseModel | dict[str, object]]
ActionPlanner = Callable[
    [RoleDefinition, BaseModel | dict[str, object], object],
    list[AgentAction] | tuple[AgentAction, ...],
]


class MainResearchRuntime:
    """Execute predefined Roles until completion or a deterministic limit."""

    def __init__(
        self,
        *,
        registry: RoleRegistry,
        role_runtime: RoleRuntime,
        execution_service: ExecutionService,
        context_builder: ContextBuilder,
        policies: Mapping[RoleId | str, RolePolicy],
        config: MainRuntimeConfig | None = None,
        projector: RoleContextProjector | None = None,
        trace: TraceSink | None = None,
        role_input_factory: RoleInputFactory | None = None,
        action_planner: ActionPlanner | None = None,
        action_executor: object | None = None,
        is_cancelled: Callable[[], bool] | None = None,
    ) -> None:
        self.registry = registry
        self.role_runtime = role_runtime
        self.execution_service = execution_service
        self.context_builder = context_builder
        self.policies = {RoleId(key): value for key, value in policies.items()}
        self.config = config or MainRuntimeConfig()
        self.projector = projector or RoleContextProjector()
        self.trace = trace
        self.role_input_factory = role_input_factory or self._default_role_input
        self.action_planner = action_planner
        self.action_executor = action_executor
        self.is_cancelled = is_cancelled or (lambda: False)

    def execute(self, *, agent_run_id: str, research_session_id: str) -> MainRuntimeResult:
        run = self.execution_service.get_agent_run(agent_run_id)
        session = self.execution_service.get_research_session(agent_run_id, research_session_id)
        usage = MainRuntimeUsage()
        results: list[RoleResult] = []
        final_response = None
        status = "running"
        reason = "unknown"
        failure_message = None
        next_role = RoleId.RESEARCH_COORDINATOR
        retrieved_evidence: list[object] = []
        self.execution_service.update_research_session_status(
            agent_run_id, research_session_id, "running"
        )
        self._trace(agent_run_id, research_session_id, "runtime.start", usage)

        while status == "running":
            reason = self._limit_reason(usage)
            if reason:
                status = "terminated"
                break
            role = self.registry.get(next_role)
            policy = self.policies.get(next_role)
            if policy is None:
                usage.failures += 1
                failure_message = f"No policy configured for Role {next_role.value}"
                status, reason = self._failure_outcome(usage)
                break
            try:
                snapshot = self.context_builder.build(
                    agent_run_id=agent_run_id,
                    research_session_id=research_session_id,
                    retrieved_evidence=retrieved_evidence,
                )
                projected = self.projector.project(snapshot, role)
                role_input = self.role_input_factory(next_role, projected)
                role_result = self.role_runtime.execute(
                    role,
                    role_input,
                    policy,
                    agent_run_id=agent_run_id,
                    research_session_id=research_session_id,
                )
            except Exception as exc:
                usage.failures += 1
                failure_message = str(exc)
                status, reason = self._failure_outcome(usage)
                self._trace(
                    agent_run_id, research_session_id, "runtime.failure", usage,
                    role_id=next_role.value, failure_message=failure_message,
                )
                if status == "running":
                    next_role = RoleId.RESEARCH_COORDINATOR
                    continue
                break

            results.append(role_result)
            usage.steps += 1
            usage.llm_calls += role_result.working_state.usage.llm_calls
            usage.tool_calls += role_result.working_state.usage.tool_calls
            self._trace(
                agent_run_id, research_session_id, "runtime.step", usage,
                role_id=next_role.value, role_execution_id=role_result.role_execution_id,
                role_status=role_result.status,
            )
            if role_result.status != "completed":
                usage.failures += 1
                failure_message = role_result.failure_message
                status, reason = self._failure_outcome(usage)
                self._trace(
                    agent_run_id, research_session_id, "runtime.failure", usage,
                    role_id=next_role.value,
                    role_execution_id=role_result.role_execution_id,
                    role_status=role_result.status,
                    failure_message=failure_message,
                )
                next_role = RoleId.RESEARCH_COORDINATOR
                continue

            output = role_result.output or {}
            if self.action_planner is not None:
                if self.action_executor is None:
                    usage.failures += 1
                    failure_message = "An action planner requires an action executor"
                    status, reason = self._failure_outcome(usage)
                    continue
                try:
                    actions = self.action_planner(role, output, projected)
                    for action in actions:
                        action_result = self.action_executor.execute(action, role)
                        if action.action_type.value == "RETRIEVE_QUERY":
                            value = getattr(action_result, "value", None)
                            retrieved_evidence = list(value or ())
                        usage.tool_calls += 1
                        self._trace(
                            agent_run_id,
                            research_session_id,
                            "runtime.action",
                            usage,
                            role_id=next_role.value,
                            role_execution_id=role_result.role_execution_id,
                            action_type=action.action_type.value,
                            action_result=self._json_value(action_result),
                        )
                except Exception as exc:
                    usage.failures += 1
                    failure_message = str(exc)
                    status, reason = self._failure_outcome(usage)
                    self._trace(
                        agent_run_id,
                        research_session_id,
                        "runtime.failure",
                        usage,
                        role_id=next_role.value,
                        role_execution_id=role_result.role_execution_id,
                        failure_message=failure_message,
                    )
                    next_role = RoleId.RESEARCH_COORDINATOR
                    continue
            if next_role == RoleId.FINAL_SYNTHESIS and output.get("completed"):
                final_response = FinalSynthesisRole.finalize(role_input, output)
                status, reason = "completed", "semantic_completion"
            elif next_role == RoleId.RESEARCH_COORDINATOR:
                selected = output.get("next_role_id")
                if output.get("completed") and selected is None:
                    status, reason = "completed", "semantic_completion"
                elif selected is None:
                    usage.failures += 1
                    failure_message = "Coordinator did not select a predefined Role"
                    status, reason = self._failure_outcome(usage)
                else:
                    next_role = RoleId(selected)
            else:
                next_role = RoleId.RESEARCH_COORDINATOR

        session_status = "completed" if status == "completed" else (
            "cancelled" if reason == "cancelled" else "failed"
        )
        self.execution_service.update_research_session_status(
            agent_run_id, research_session_id, session_status
        )
        self._trace(
            agent_run_id, research_session_id, "runtime.completion", usage,
            status=status, termination_reason=reason,
        )
        return MainRuntimeResult(
            agent_run_id=agent_run_id,
            research_session_id=research_session_id,
            status=status,
            termination_reason=reason,
            usage=usage,
            role_results=results,
            final_response=final_response,
            failure_message=failure_message,
        )

    def _limit_reason(self, usage: MainRuntimeUsage) -> str | None:
        if self.is_cancelled():
            return "cancelled"
        if usage.steps >= self.config.max_steps:
            return "max_steps"
        if usage.llm_calls >= self.config.max_llm_calls:
            return "max_llm_calls"
        if usage.tool_calls >= self.config.max_tool_calls:
            return "max_tool_calls"
        if usage.failures >= self.config.max_failures:
            return "max_failures"
        return None

    def _failure_outcome(self, usage: MainRuntimeUsage) -> tuple[str, str]:
        if usage.failures >= self.config.max_failures:
            return "failed", "max_failures"
        return "running", "role_failure_recovered"

    @staticmethod
    def _default_role_input(role_id: RoleId, context: object) -> dict[str, object]:
        sections = getattr(context, "sections")
        session = sections.get("session", {})
        research_session = session.get("research_session", {})
        session_id = research_session.get("research_session_id", "")
        question = research_session.get("research_question", "")
        if role_id == RoleId.RESEARCH_COORDINATOR:
            return {"research_session_id": session_id, "research_goal": question}
        if role_id == RoleId.QUERY_PLANNING:
            return {"research_session_id": session_id, "research_question": question}
        if role_id == RoleId.EVIDENCE_REASONING:
            return {
                "research_session_id": session_id,
                "evidence_ids": [item["evidence_id"] for item in sections.get("retrieved_evidence", [])],
            }
        if role_id == RoleId.CLAIM_REASONING:
            return {
                "research_session_id": session_id,
                "accepted_evidence_ids": [item["evidence_id"] for item in sections.get("accepted_evidence", [])],
            }
        return {
            "research_session_id": session_id,
            "claims": sections.get("claims", ()),
            "accepted_evidence": sections.get("accepted_evidence", ()),
            "claim_evidence_links": sections.get("claim_evidence_links", ()),
        }

    def _trace(
        self, agent_run_id: str, research_session_id: str, event_type: str,
        usage: MainRuntimeUsage, **payload: Any,
    ) -> None:
        if self.trace is not None:
            self.trace.append_event(
                agent_run_id=agent_run_id,
                research_session_id=research_session_id,
                event_type=event_type,
                payload={"usage": usage.model_dump(), **payload},
            )

    @staticmethod
    def _json_value(value: object) -> Any:
        if isinstance(value, BaseModel):
            return value.model_dump(mode="json")
        if isinstance(value, (str, int, float, bool, list, dict)) or value is None:
            return value
        return repr(value)


AgentRuntime = MainResearchRuntime

__all__ = [
    "AgentRuntime", "FinalResponseArtifact", "MainResearchRuntime",
    "MainRuntimeResult", "MainRuntimeUsage",
]
