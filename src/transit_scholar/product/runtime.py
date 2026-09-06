"""Durable product-layer runtime state adapters."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from uuid import uuid4


@dataclass(slots=True)
class RunScope:
    """Disposable, non-owning composition of services for one AgentRun."""

    agent_run: Any
    session: Any
    workspace: Any
    execution: Any
    research_state: Any
    ledger: Any
    trace: Any
    knowledge: Any
    role_registry: Any
    action_validator: Any
    action_executor: Any
    role_runtime: Any
    main_runtime: Any
    coordinator: Any
    run_runtime: Any
    episodic_memory: Any
    l3s7_lifecycle: Any

    @property
    def agent_run_id(self) -> str:
        return self.agent_run.agent_run_id

    @property
    def workspace_id(self) -> str:
        return self.agent_run.workspace_id

    @property
    def workspace_revision(self) -> int:
        return self.agent_run.workspace_revision

    @property
    def run_research_runtime(self):
        return self.run_runtime

    def close(self) -> None:
        self.session.close()


class RuntimeFactory:
    """Official product-layer composition path for an existing AgentRun."""

    def __init__(self, *, session_factory=None, settings=None, data_root=None,
                 llm_client=None, role_registry=None, policies=None,
                 semantic_decider=None, coordinator=None, main_config=None,
                 run_config=None, episodic_memory=None, l3s7_lifecycle=None,
                 state_store=None, role_store=None, knowledge=None,
                 runtime_root=None):
        from transit_scholar.db.engine import SessionLocal
        from transit_scholar.config import settings as default_settings
        self.session_factory = session_factory or SessionLocal
        self.settings = settings or default_settings
        self.data_root = data_root or getattr(self.settings, "data_root", None)
        self.llm_client = llm_client
        self.role_registry = role_registry
        self.policies = policies
        self.semantic_decider = semantic_decider
        self.coordinator = coordinator
        self.main_config = main_config
        self.run_config = run_config
        self.episodic_memory = episodic_memory
        self.l3s7_lifecycle = l3s7_lifecycle
        self.state_store = state_store
        self.role_store = role_store
        self.knowledge = knowledge
        self.runtime_root = runtime_root or (Path(self.data_root) / "layer3" / "runs" if self.data_root else Path("data/layer3/runs"))

    def build_run_scope(self, agent_run_id: str) -> RunScope:
        from transit_scholar.layer3.execution import AgentRunService
        from transit_scholar.layer3.state import ResearchStateService
        from transit_scholar.layer3.ledger import ResearchReasoningLedgerService
        from transit_scholar.layer3.trace import AgentTraceService
        from transit_scholar.layer3.grounding import WorkspaceGroundingService
        from transit_scholar.layer3.knowledge import WorkspaceKnowledgeGateway
        from transit_scholar.layer3.agent import built_in_role_registry
        from transit_scholar.layer3.actions import ActionValidator, ActionExecutor
        from transit_scholar.layer3.runtime import RoleRuntime, MainResearchRuntime, RunResearchRuntime, FileRoleExecutionStore
        from transit_scholar.layer3.context import RuntimeContextSnapshotBuilder
        from transit_scholar.layer3.memory import L3S7Lifecycle, EpisodicMemoryRetriever
        from transit_scholar.layer3.roles import build_run_coordinator
        from .roles import StructuredLLMRolePolicy, BuiltinRoleActionPlanner

        session = self.session_factory()
        execution = AgentRunService(session)
        run = execution.get_agent_run(agent_run_id)
        workspace_service = execution.workspaces
        grounding = WorkspaceGroundingService(session, data_root=self.data_root, workspaces=workspace_service)
        workspace = grounding.ground(run.workspace_id)
        knowledge = self.knowledge or WorkspaceKnowledgeGateway(
            session, workspace_id=run.workspace_id, expected_revision=run.workspace_revision,
            data_root=self.data_root, workspaces=workspace_service,
        )
        research_state = ResearchStateService(session)
        ledger = ResearchReasoningLedgerService(session)
        trace = AgentTraceService(session)
        registry = self.role_registry or built_in_role_registry()
        policy = StructuredLLMRolePolicy(self.llm_client)
        policies = self.policies or {definition.role_id: policy for definition in registry.list()}
        validator = ActionValidator(execution_service=execution, ledger_service=ledger, role_registry=registry)
        role_invoker = lambda target, role_input: None
        action_executor = ActionExecutor(validator=validator, execution_service=execution, ledger_service=ledger, knowledge_service=knowledge, role_invoker=role_invoker)
        role_store = self.role_store or FileRoleExecutionStore(self.runtime_root / agent_run_id / "roles")
        role_runtime = RoleRuntime(registry, role_store, trace=trace, action_executor=action_executor)
        context_builder = RuntimeContextSnapshotBuilder(session, grounding=grounding)
        main_runtime = MainResearchRuntime(registry=registry, role_runtime=role_runtime, execution_service=execution,
            context_builder=context_builder, policies=policies, config=self.main_config,
            trace=trace, action_planner=BuiltinRoleActionPlanner(), action_executor=action_executor,
            workspace_service=workspace_service, ledger_service=ledger,
            state_store=research_state)
        coordinator = self.coordinator or build_run_coordinator(semantic_decider=self.semantic_decider, llm_client=self.llm_client)
        run_store = _CommitBeforeCheckpointStore(
            session, self.state_store or FileRunResearchStateStore(self.runtime_root)
        )
        lifecycle = self.l3s7_lifecycle or L3S7Lifecycle.for_workspace(run.workspace_id, data_root=self.data_root,
            workspace_service=workspace_service, ledger_service=ledger, execution_service=execution)
        memory = self.episodic_memory or EpisodicMemoryRetriever(lifecycle.episodic_store)
        run_runtime = RunResearchRuntime(session_runtime=main_runtime, coordinator=coordinator, execution_service=execution,
            ledger_service=ledger, trace=trace, config=self.run_config, state_store=run_store,
            l3s7_lifecycle=lifecycle, episodic_memory_retriever=memory)
        return RunScope(run, session, workspace, execution, research_state, ledger, trace, knowledge, registry,
            validator, action_executor, role_runtime, main_runtime, coordinator, run_runtime, memory, lifecycle)

    build = build_run_scope


class FileRunResearchStateStore:
    """Atomically persist ``RunResearchRuntime`` state by AgentRun identifier."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def save(self, agent_run_id: str, payload: Mapping[str, Any]) -> None:
        target = self._path(agent_run_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
        try:
            serialized = json.dumps(dict(payload), indent=2, sort_keys=True) + "\n"
            with temporary.open("w", encoding="utf-8") as output:
                output.write(serialized)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)

    def load(self, agent_run_id: str) -> dict[str, Any] | None:
        target = self._path(agent_run_id)
        if not target.exists():
            return None
        payload = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("run state payload must be a JSON object")
        return payload

    def _path(self, agent_run_id: str) -> Path:
        if not isinstance(agent_run_id, str) or not agent_run_id or Path(agent_run_id).name != agent_run_id:
            raise ValueError("agent_run_id must be a non-empty file-safe identifier")
        return self.root / agent_run_id / "run_state.json"


class _CommitBeforeCheckpointStore:
    """Commit authoritative SQL state before publishing a run checkpoint."""

    def __init__(self, session: Any, delegate: Any) -> None:
        self._session = session
        self._delegate = delegate

    def save(self, agent_run_id: str, payload: Mapping[str, Any]) -> None:
        self._session.commit()
        if hasattr(self._delegate, "save"):
            self._delegate.save(agent_run_id, payload)
        elif hasattr(self._delegate, "save_state"):
            self._delegate.save_state(agent_run_id=agent_run_id, payload=payload)
        elif hasattr(self._delegate, "set"):
            self._delegate.set(agent_run_id, payload)
        else:
            raise TypeError("run state store must provide save, save_state, or set")

    def load(self, agent_run_id: str) -> Any:
        if hasattr(self._delegate, "load"):
            return self._delegate.load(agent_run_id)
        if hasattr(self._delegate, "load_state"):
            return self._delegate.load_state(agent_run_id=agent_run_id)
        if hasattr(self._delegate, "get"):
            return self._delegate.get(agent_run_id)
        raise TypeError("run state store must provide load, load_state, or get")
