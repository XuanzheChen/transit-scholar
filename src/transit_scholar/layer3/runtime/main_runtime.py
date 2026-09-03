"""Plain-Python orchestration loop for one ResearchSession."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Protocol
from uuid import uuid4

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
from transit_scholar.layer3.context import RetrievedEvidenceContext, RoleContextProjector
from transit_scholar.layer3.memory import EpisodicMemoryRetriever
from transit_scholar.layer3.tools import RetrievalResultEnvelope

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


class MainRuntimeState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_run_id: str
    research_session_id: str
    status: str = "running"
    next_role_id: RoleId = RoleId.RESEARCH_COORDINATOR
    current_role_execution_id: str | None = None
    usage: MainRuntimeUsage = Field(default_factory=MainRuntimeUsage)
    role_results: list[RoleResult] = Field(default_factory=list)
    latest_retrieval_observation: list[dict[str, Any]] = Field(default_factory=list)
    final_response: FinalResponseArtifact | None = None
    termination_reason: str | None = None
    failure_message: str | None = None
    session_handoff: Any | None = None


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


class MainRuntimeStateStore(Protocol):
    def save_research_state(self, **kwargs: Any) -> object: ...
    def load_research_state(self, **kwargs: Any) -> object: ...


RoleInputFactory = Callable[[RoleId, object], BaseModel | dict[str, object]]
ActionPlanner = Callable[
    [RoleDefinition, BaseModel | dict[str, object], object],
    list[AgentAction] | tuple[AgentAction, ...],
]


class MainResearchRuntime:
    """Execute predefined Roles until completion or a deterministic limit."""

    requires_authoritative_session = True
    requires_execution_service = True

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
        state_store: MainRuntimeStateStore | None = None,
        agentic_wiki_maintenance: Callable[[str], object] | None = None,
        agentic_wiki_base_dir: str | None = None,
        workspace_service: object | None = None,
        ledger_service: object | None = None,
        episodic_memory_retriever: EpisodicMemoryRetriever | None = None,
        episodic_memory_top_k: int = 5,
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
        if (
            action_executor is not None
            and getattr(self.role_runtime, "action_executor", None) is None
        ):
            self.role_runtime.action_executor = action_executor
        self.is_cancelled = is_cancelled or (lambda: False)
        self.state_store = state_store
        self._agentic_wiki_maintenance_explicit = agentic_wiki_maintenance is not None
        self.agentic_wiki_maintenance = agentic_wiki_maintenance
        if self.agentic_wiki_maintenance is None:
            self.agentic_wiki_maintenance = self._default_agentic_wiki_maintenance(
                base_dir=agentic_wiki_base_dir,
                workspace_service=workspace_service,
                ledger_service=ledger_service,
            )
        if episodic_memory_top_k < 0:
            raise ValueError("episodic_memory_top_k must not be negative")
        self.episodic_memory_retriever = episodic_memory_retriever
        self.episodic_memory_top_k = episodic_memory_top_k

    def _default_agentic_wiki_maintenance(
        self,
        *,
        base_dir: str | None,
        workspace_service: object | None,
        ledger_service: object | None,
    ) -> Callable[[str], object] | None:
        """Compose the production Session-start provenance boundary lazily.

        Existing callers that provide an explicit hook keep that hook.  The
        normal authoritative ``AgentRunService`` path supplies a SQLAlchemy
        session, from which Workspace and L3S4 readers can be composed without
        caller-managed resolver collections.
        """
        execution = self.execution_service
        db_session = getattr(execution, "session", None)
        if workspace_service is None:
            workspace_service = getattr(execution, "workspaces", None)
        if db_session is None and workspace_service is not None:
            db_session = getattr(workspace_service, "session", None)
        if ledger_service is None and db_session is not None:
            try:
                from transit_scholar.layer3.ledger import ResearchReasoningLedgerService

                ledger_service = ResearchReasoningLedgerService(db_session)
            except (ImportError, TypeError):
                ledger_service = None
        if workspace_service is None and db_session is not None:
            try:
                from transit_scholar.layer3.workspace import WorkspaceService

                workspace_service = WorkspaceService(db_session)
            except (ImportError, TypeError):
                workspace_service = None
        if workspace_service is None and ledger_service is None:
            return None

        def maintain(workspace_id: str) -> object:
            from transit_scholar.layer3.agentic_wiki import AgenticWikiMaintenance

            return AgenticWikiMaintenance.for_workspace(
                workspace_id,
                base_dir=base_dir,
                workspace_service=workspace_service,
                ledger_service=ledger_service,
                execution_service=execution,
            )(workspace_id)

        return maintain

    def execute(self, *, agent_run_id: str, research_session_id: str, session_handoff: object | None = None) -> MainRuntimeResult:
        state = MainRuntimeState(
            agent_run_id=agent_run_id, research_session_id=research_session_id,
            session_handoff=session_handoff,
        )
        return self._execute_state(state, resumed=False, session_handoff=session_handoff)

    def resume_session(
        self, *, agent_run_id: str, research_session_id: str,
        session_handoff: object | None = None,
    ) -> MainRuntimeResult:
        if self.state_store is None:
            raise RuntimeError("resume_session requires a durable Main Runtime state store")
        record = self.state_store.load_research_state(
            agent_run_id=agent_run_id, research_session_id=research_session_id
        )
        payload = getattr(record, "payload", None) if record is not None else None
        if not isinstance(payload, dict) or "l3s5" not in payload:
            raise LookupError("No durable L3S5 continuation state exists for this session")
        state = MainRuntimeState.model_validate(payload["l3s5"])
        if (
            state.agent_run_id != agent_run_id
            or state.research_session_id != research_session_id
        ):
            raise ValueError("Persisted Main Runtime state belongs to another session")
        handoff = state.session_handoff if state.session_handoff is not None else session_handoff
        return self._execute_state(state, resumed=True, session_handoff=handoff)

    def _execute_state(self, state: MainRuntimeState, *, resumed: bool, session_handoff: object | None = None) -> MainRuntimeResult:
        agent_run_id = state.agent_run_id
        research_session_id = state.research_session_id
        run = self.execution_service.get_agent_run(agent_run_id)
        if self.agentic_wiki_maintenance is not None:
            workspace_id = getattr(run, "workspace_id", None)
            if workspace_id is None and isinstance(run, Mapping):
                workspace_id = run.get("workspace_id")
            if workspace_id:
                self.agentic_wiki_maintenance(str(workspace_id))
        session = self.execution_service.get_research_session(agent_run_id, research_session_id)
        usage = state.usage
        results = state.role_results
        final_response = state.final_response
        status = state.status
        reason = state.termination_reason or "unknown"
        failure_message = state.failure_message
        if state.session_handoff is not None:
            session_handoff = state.session_handoff
        elif session_handoff is not None:
            state.session_handoff = session_handoff
        next_role = state.next_role_id
        retrieved_evidence: list[object] = list(state.latest_retrieval_observation)
        if status != "running":
            return self._state_result(state)
        self.execution_service.update_research_session_status(
            agent_run_id, research_session_id, "running"
        )
        self._trace(
            agent_run_id, research_session_id,
            "runtime.resume" if resumed else "runtime.start", usage,
        )
        self._save_state(state)

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
                build_kwargs = {
                    "agent_run_id": agent_run_id,
                    "research_session_id": research_session_id,
                    "retrieved_evidence": retrieved_evidence,
                }
                if session_handoff is not None:
                    build_kwargs["session_handoff"] = session_handoff
                if (
                    next_role == RoleId.QUERY_PLANNING
                    and self.episodic_memory_retriever is not None
                ):
                    build_kwargs["episodic_memory"] = self._retrieve_episodic_memory(
                        run, session
                    )
                snapshot = self.context_builder.build(**build_kwargs)
                projected = self.projector.project(snapshot, role)
                role_input = self.role_input_factory(next_role, projected)
                role_execution_id = state.current_role_execution_id or uuid4().hex
                state.current_role_execution_id = role_execution_id
                state.next_role_id = next_role
                self._save_state(state)
                role_result = self.role_runtime.execute(
                    role,
                    role_input,
                    policy,
                    agent_run_id=agent_run_id,
                    research_session_id=research_session_id,
                    role_context=projected,
                    action_planner=(
                        (
                            lambda definition, output, context: self.action_planner(
                                definition,
                                output.model_dump(mode="json")
                                if isinstance(output, BaseModel)
                                else output,
                                context,
                            )
                        )
                        if self.action_planner is not None
                        else None
                    ),
                    role_execution_id=role_execution_id,
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
            state.current_role_execution_id = None
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
            # Specialist actions are executed inside RoleRuntime. Retrieval
            # observations are recovered from the committed action boundary.
            if role_result.working_state.intermediate_artifacts:
                for artifact in role_result.working_state.intermediate_artifacts:
                    action = artifact.get("action", {})
                    self._trace(
                        agent_run_id,
                        research_session_id,
                        "runtime.action",
                        usage,
                        role_id=next_role.value,
                        role_execution_id=role_result.role_execution_id,
                        action_type=action.get("action_type"),
                        action_result=artifact.get("result"),
                    )
                    if action.get("action_type") == "RETRIEVE_QUERY":
                        value = artifact.get("result")
                        if isinstance(value, dict) and "value" in value:
                            value = value["value"]
                        if isinstance(value, RetrievalResultEnvelope):
                            value = value.evidence_results
                        if isinstance(value, dict) and "evidence_results" in value:
                            value = value["evidence_results"]
                        retrieved_evidence = [
                            item
                            if isinstance(item, dict)
                            else RetrievedEvidenceContext(
                                evidence_id=item.evidence_id,
                                payload=item.model_dump(mode="json"),
                            ).model_dump(mode="json")
                            for item in (value or [])
                        ]
                        state.latest_retrieval_observation = list(retrieved_evidence)
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
            state.next_role_id = next_role
            state.usage = usage
            state.role_results = results
            state.status = status
            state.termination_reason = reason
            state.failure_message = failure_message
            state.final_response = final_response
            self._save_state(state)

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
        state.status = status
        state.termination_reason = reason
        state.failure_message = failure_message
        state.final_response = final_response
        state.usage = usage
        state.role_results = results
        self._save_state(state)
        return self._state_result(state)

    @staticmethod
    def _state_result(state: MainRuntimeState) -> MainRuntimeResult:
        return MainRuntimeResult(
            agent_run_id=state.agent_run_id,
            research_session_id=state.research_session_id,
            status=state.status,
            termination_reason=state.termination_reason,
            usage=state.usage,
            role_results=state.role_results,
            final_response=state.final_response,
            failure_message=state.failure_message,
        )

    def _save_state(self, state: MainRuntimeState) -> None:
        if self.state_store is None:
            return
        existing = self.state_store.load_research_state(
            agent_run_id=state.agent_run_id,
            research_session_id=state.research_session_id,
        )
        payload = dict(getattr(existing, "payload", {}) or {})
        payload["l3s5"] = state.model_dump(mode="json")
        self.state_store.save_research_state(
            agent_run_id=state.agent_run_id,
            research_session_id=state.research_session_id,
            payload=payload,
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

    def _retrieve_episodic_memory(self, run: object, session: object) -> tuple[object, ...]:
        """Select bounded Workspace experience for QueryPlanningRole only."""
        if self.episodic_memory_retriever is None or self.episodic_memory_top_k == 0:
            return ()
        workspace_id = self._field(run, "workspace_id")
        if not workspace_id:
            return ()
        query = self._field(session, "research_question") or self._field(
            run, "user_goal"
        )
        if not query:
            return ()
        return self.episodic_memory_retriever.retrieve(
            workspace_id=str(workspace_id),
            query=str(query),
            top_k=self.episodic_memory_top_k,
        )

    @staticmethod
    def _field(value: object, name: str) -> object | None:
        if isinstance(value, Mapping):
            return value.get(name)
        return getattr(value, name, None)

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
    "MainRuntimeResult", "MainRuntimeState", "MainRuntimeStateStore", "MainRuntimeUsage",
]
