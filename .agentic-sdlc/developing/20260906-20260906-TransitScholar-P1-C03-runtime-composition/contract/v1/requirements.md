# Requirements

## REQ-001

Title: Provide one official RuntimeFactory for AgentRun composition

Description:
The Product Composition Layer MUST provide one `RuntimeFactory` that can build all production runtime dependencies required to execute an existing AgentRun. Callers MUST be able to request a run scope by AgentRun identifier without manually assembling Agent Core services in tests, API code, or future UI code.

Priority: Must

## REQ-002

Title: Build a non-owning RunScope for one AgentRun

Description:
The RuntimeFactory MUST return a `RunScope` representing the correctly bound object graph for one AgentRun. RunScope MUST contain references to the composed runtime/services needed for execution but MUST NOT become an authoritative state store or duplicate AgentRun, ResearchSession, ledger, memory, wiki, or trace state.

Priority: Must

## REQ-003

Title: Bind Workspace-scoped knowledge and retrieval from authoritative AgentRun identity

Description:
RunScope composition MUST load the authoritative AgentRun and use its existing `workspace_id` and `workspace_revision` to construct Workspace-bound knowledge/retrieval components. A RunScope for one Workspace MUST NOT retrieve or admit knowledge belonging to another Workspace.

Priority: Must

## REQ-004

Title: Compose existing Core services into the existing run/session runtimes

Description:
RunScope MUST compose the existing authoritative execution, research-state, ledger, trace, Workspace knowledge/retrieval, Role bridge, ActionValidator, ActionExecutor, RoleRuntime, MainResearchRuntime, production run coordinator, RunResearchRuntime, Episodic Memory, and L3S7/Agentic Wiki lifecycle objects required by the current Agent Core. Existing RunResearchRuntime and MainResearchRuntime MUST remain the only run-level and ResearchSession-level orchestration loops.

Priority: Must

## REQ-005

Title: Persist RunResearchRuntime orchestration state durably

Description:
The Product Composition Layer MUST provide a durable local store for the existing RunResearchRuntime `state_store` seam so run-level orchestration state can be reloaded after rebuilding a RunScope. The store MUST be keyed by AgentRun, use local durable storage, and write atomically.

Priority: Must

## REQ-006

Title: Preserve durable checkpoint ordering between SQL state and run state

Description:
Production RunScope execution MUST ensure that authoritative SQLAlchemy mutations are committed before a durable run-orchestration checkpoint claims progress beyond those mutations. This behavior MUST use the existing transaction and runtime checkpoint mechanisms and MUST NOT introduce a new Unit-of-Work framework.

Priority: Must

## REQ-007

Title: Rebuild and recover an existing AgentRun scope

Description:
Given an existing AgentRun and its durable Core/runtime state, RuntimeFactory MUST be able to construct a fresh RunScope that points to the same authoritative Workspace and AgentRun and allows the existing Core recovery/resume behavior to read its persisted state.

Priority: Must
