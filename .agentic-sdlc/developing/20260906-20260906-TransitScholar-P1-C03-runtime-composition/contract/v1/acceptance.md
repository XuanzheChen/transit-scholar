# Acceptance Criteria

## AC-001

Requirements:
- REQ-001
- REQ-002

Criterion:
Given a valid existing AgentRun identifier, `RuntimeFactory.build_run_scope(agent_run_id)` MUST return one RunScope containing a configured RunResearchRuntime and its required production dependencies without caller-side manual composition.

## AC-002

Requirements:
- REQ-002

Criterion:
RunScope MUST not persist a second copy of AgentRun, ResearchSession, query, evidence, claim, memory, wiki, or trace state and MUST be disposable after the run invocation.

## AC-003

Requirements:
- REQ-003

Criterion:
When a RunScope is built, its Workspace-bound knowledge/retrieval components MUST use the same `workspace_id` and recorded Workspace revision as the authoritative AgentRun.

## AC-004

Requirements:
- REQ-003

Criterion:
A retrieval performed through a RunScope for Workspace A MUST NOT return or admit Workspace B-only content when existing Workspace isolation rules would reject that access.

## AC-005

Requirements:
- REQ-004

Criterion:
RunScope MUST install the production Role policy, deterministic Role action planner, ActionValidator, ActionExecutor, RoleRuntime, MainResearchRuntime, production run coordinator, and RunResearchRuntime using existing Core contracts rather than alternate orchestration code.

## AC-006

Requirements:
- REQ-004

Criterion:
RunResearchRuntime MUST receive the existing L3S7 lifecycle and Episodic Memory integration required by the current Core so session-start maintenance and run-completion knowledge evolution continue through existing lifecycle behavior.

## AC-007

Requirements:
- REQ-005

Criterion:
Saving run-orchestration state MUST create or replace one AgentRun-keyed durable local state representation atomically, and loading it from a newly created store instance MUST reproduce the saved RunResearchRuntime state needed by the existing runtime seam.

## AC-008

Requirements:
- REQ-005
- REQ-007

Criterion:
After rebuilding RuntimeFactory/RunScope in a fresh process-equivalent test context, the new RunScope MUST be able to read the previously persisted run-level orchestration state for the same AgentRun.

## AC-009

Requirements:
- REQ-006

Criterion:
At a production run-state checkpoint boundary following authoritative SQLAlchemy mutations, the relevant database transaction MUST be committed before the corresponding durable run-state file is atomically published.

## AC-010

Requirements:
- REQ-007

Criterion:
Rebuilding a RunScope for an existing AgentRun MUST preserve the original AgentRun identifier, Workspace identifier, Workspace revision, and existing Core state ownership; it MUST NOT create a replacement AgentRun.

## AC-011

Requirements:
- REQ-004

Criterion:
Product-layer code in this contract MUST NOT implement a second `for/while` orchestration loop that decides ResearchSession creation, Role progression, replanning, or final synthesis outside existing RunResearchRuntime/MainResearchRuntime.

## AC-012

Requirements:
- REQ-001
- REQ-004
- REQ-007

Criterion:
A fixture-backed AgentRun MUST be executable through `RuntimeFactory.build_run_scope(run_id).run_runtime.execute(...)` with no test-side manual construction of Agent Core dependencies.
