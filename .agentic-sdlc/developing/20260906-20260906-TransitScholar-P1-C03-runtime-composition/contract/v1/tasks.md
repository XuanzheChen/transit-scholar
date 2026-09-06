# Task Breakdown

## T-001

Title: Implement durable RunResearchRuntime state storage

Goal:
Provide an AgentRun-keyed durable local state store compatible with the existing RunResearchRuntime state-store seam, including atomic writes and reload behavior.

Requirements:
- REQ-005

Acceptance Criteria:
- AC-007
- AC-008

Dependencies:
- None

Allowed Scope:
- src/transit_scholar/product/runtime.py
- src/transit_scholar/product/**
- tests/product/**
- tests/layer3/**

Forbidden Scope:
- src/transit_scholar/layer3/runtime/run_runtime.py

Implementation Notes:
- Follow the existing FileRoleExecutionStore atomic-write pattern where practical.
- Keep the stored payload limited to what the existing RunResearchRuntime seam requires.
- Key storage by AgentRun identity.

Required Verification:
- python -m pytest tests/product tests/layer3 -q

## T-002

Title: Implement RuntimeFactory and non-owning RunScope

Goal:
Create the single product-layer composition path that loads an existing AgentRun and builds the authoritative services and runtime dependencies required for execution.

Requirements:
- REQ-001
- REQ-002
- REQ-003
- REQ-004

Acceptance Criteria:
- AC-001
- AC-002
- AC-003
- AC-004
- AC-005
- AC-006
- AC-011

Dependencies:
- T-001

Allowed Scope:
- src/transit_scholar/product/runtime.py
- src/transit_scholar/product/**
- tests/product/**
- tests/layer3/**

Forbidden Scope:
- src/transit_scholar/layer3/runtime/**
- src/transit_scholar/layer3/run_context/**
- src/transit_scholar/layer3/memory/**
- src/transit_scholar/layer3/agentic_wiki/**

Implementation Notes:
- Consume the production Role bridge provided by the preceding Role Production Bridge contract.
- Reuse existing builders and lifecycle factories where they already exist.
- Build Workspace-bound retrieval after loading the authoritative AgentRun and its Workspace revision.
- Do not add orchestration loops.

Required Verification:
- python -m pytest tests/product tests/layer3 -q

## T-003

Title: Enforce production checkpoint commit ordering

Goal:
Ensure the RunResearchRuntime production state-store path commits preceding authoritative SQLAlchemy mutations before atomically publishing a durable run-state checkpoint.

Requirements:
- REQ-006

Acceptance Criteria:
- AC-009

Dependencies:
- T-001
- T-002

Allowed Scope:
- src/transit_scholar/product/runtime.py
- src/transit_scholar/product/**
- tests/product/**
- tests/layer3/**

Forbidden Scope:
- src/transit_scholar/db/**
- src/transit_scholar/layer3/runtime/**

Implementation Notes:
- Use a thin production wrapper or callback around the state-store save boundary.
- Do not introduce a Unit-of-Work framework.
- Tests should prove ordering, not only eventual persistence.

Required Verification:
- python -m pytest tests/product tests/layer3 -q

## T-004

Title: Verify RunScope rebuild, isolation, and Core recovery compatibility

Goal:
Add fixture-backed integration coverage showing one AgentRun can be executed through RuntimeFactory alone, its scope can be rebuilt from durable state, Workspace isolation is preserved, and no second orchestrator is introduced.

Requirements:
- REQ-001
- REQ-002
- REQ-003
- REQ-004
- REQ-005
- REQ-006
- REQ-007

Acceptance Criteria:
- AC-001
- AC-002
- AC-003
- AC-004
- AC-005
- AC-006
- AC-007
- AC-008
- AC-009
- AC-010
- AC-011
- AC-012

Dependencies:
- T-001
- T-002
- T-003

Allowed Scope:
- tests/product/**
- tests/layer3/**

Forbidden Scope:
- None

Implementation Notes:
- The acceptance path must call `RuntimeFactory.build_run_scope(run_id).run_runtime.execute(...)`.
- The test must not manually build Core services outside RuntimeFactory.
- Include a fresh-factory or process-equivalent reconstruction step for durable recovery coverage.

Required Verification:
- python -m pytest tests/product tests/layer3 -q
