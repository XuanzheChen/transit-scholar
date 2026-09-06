# Constraints

## C-001

RunResearchRuntime MUST remain the only run-level orchestrator and MainResearchRuntime MUST remain the only ResearchSession-level Role orchestrator.

## C-002

RunScope MUST be non-owning composition state only and MUST NOT become a new authoritative persistence model.

## C-003

Workspace-scoped retrieval/knowledge objects MUST NOT be global singletons shared across unrelated Workspaces.

## C-004

This contract MUST NOT introduce a DI framework, service container framework, queue, worker system, distributed execution, Redis, Celery, message bus, or cloud/deployment abstraction.

## C-005

Existing Core persistence for AgentRun, ResearchSession, ResearchState, ledger data, trace, RoleExecution, Episodic Memory, and Agentic Wiki MUST remain authoritative.

## C-006

The new run-orchestration state store MUST use local durable storage and atomic replacement; it MUST NOT require a new database table unless the existing code already makes that strictly necessary for the current runtime seam.

## C-007

This contract MUST NOT implement API routes, UI behavior, conversation persistence, or product-level submit-message behavior.
