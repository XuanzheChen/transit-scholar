# Task Breakdown

## T-001

Title: Introduce Application-Scope API Runtime Context

Goal:
Refactor API bootstrap so migrations and expensive runtime composition occur once per FastAPI process while each request and worker creates only a fresh Product/session facade.

Requirements:
- REQ-002
- REQ-021

Acceptance Criteria:
- AC-003
- AC-004
- AC-005
- AC-037

Dependencies:
- None

Allowed Scope:
- src/transit_scholar/api/**
- src/transit_scholar/product/**
- tests/api/**
- tests/product/**

Forbidden Scope:
- src/transit_scholar/layer3/roles/**
- src/transit_scholar/layer3/ledger/**
- src/transit_scholar/layer3/memory/**

Implementation Notes:
- Add an `ApiRuntimeContext` or equivalent lifespan-owned composition object.
- Run full product bootstrap/migration once.
- Reuse process-safe RuntimeFactory/provider/SchemaCatalog dependencies.
- Create request-owned and worker-owned SQLAlchemy Sessions independently.

Required Verification:
- python -m pytest tests/api tests/product -q
- Add a regression proving two HTTP requests do not invoke full bootstrap twice.
- Add a regression proving request Sessions are distinct.
- Add a worker-scope regression proving execution remains valid after request closure.


## T-002

Title: Make Prompt Admission Atomic

Goal:
Eliminate the race between runner-busy checking and `prepare_message()` so rejected concurrent prompt submissions cannot leave orphan Turns or AgentRuns.

Requirements:
- REQ-003
- REQ-005

Acceptance Criteria:
- AC-006
- AC-007
- AC-010

Dependencies:
- T-001

Allowed Scope:
- src/transit_scholar/api/runtime/**
- src/transit_scholar/api/routers/conversations.py
- src/transit_scholar/product/**
- tests/api/**
- tests/product/**

Forbidden Scope:
- src/transit_scholar/layer1/**
- src/transit_scholar/layer2/**
- src/transit_scholar/layer3/roles/**
- src/transit_scholar/layer3/retrieval/**

Implementation Notes:
- Replace `is_busy -> prepare_message -> submit` with atomic slot reservation.
- Busy rejection must happen before Product mutation.
- Release reservation on preparation failure.
- Ensure scheduling failure cannot leave a Workspace-blocking orphan run.

Required Verification:
- python -m pytest tests/api tests/product -q
- Add a two-request concurrency race test.
- Verify exactly one Turn/Run is persisted.
- Verify rejected request returns 409 `RUNNER_BUSY`.
- Verify no extra non-terminal run remains.


## T-003

Title: Harden LocalExecutionManager Lifecycle

Goal:
Make execution slot ownership, executor submission, Future retention, and worker cleanup exception-safe and bounded.

Requirements:
- REQ-004

Acceptance Criteria:
- AC-008
- AC-009

Dependencies:
- T-001
- T-002

Allowed Scope:
- src/transit_scholar/api/runtime/**
- tests/api/**

Forbidden Scope:
- src/transit_scholar/layer1/**
- src/transit_scholar/layer2/**
- src/transit_scholar/layer3/**

Implementation Notes:
- Release active reservation if executor submission raises.
- Clear active ownership in every terminal path.
- Remove completed Futures or keep only bounded terminal retention.
- Preserve exactly one active worker.

Required Verification:
- python -m pytest tests/api -q
- Add executor-submit failure injection test.
- Add completed-Future cleanup test.
- Add repeated sequential-run test.


## T-004

Title: Propagate Cooperative Pause into Active Session Runtime

Goal:
Move pause observation below run-level session boundaries so an active ResearchSession can pause at the nearest supported safe checkpoint while preserving resumable semantics.

Requirements:
- REQ-006
- REQ-007

Acceptance Criteria:
- AC-011
- AC-012
- AC-013
- AC-014

Dependencies:
- T-003

Allowed Scope:
- src/transit_scholar/api/runtime/**
- src/transit_scholar/product/**
- src/transit_scholar/layer3/runtime/**
- src/transit_scholar/layer3/execution/**
- tests/api/**
- tests/product/**
- tests/layer3/**

Forbidden Scope:
- src/transit_scholar/layer3/roles/**
- src/transit_scholar/layer3/ledger/**
- src/transit_scholar/layer3/memory/**
- src/transit_scholar/layer3/knowledge_evolution/**

Implementation Notes:
- Reuse existing durable checkpoint/recovery mechanisms.
- Add pause checks at existing safe boundaries.
- Do not translate pause into cancellation.
- Preserve the same AgentRun and active ResearchSession for resume.

Required Verification:
- python -m pytest tests/layer3 tests/product tests/api -q
- Add pause-during-active-session regression.
- Prove pause can occur at an intermediate supported checkpoint.
- Prove resume continues the same AgentRun.
- Prove cancel semantics remain distinct.


## T-005

Title: Sanitize Public Run Timeline

Goal:
Ensure Timeline remains useful for progress inspection while preventing raw exception/provider/private reasoning leakage.

Requirements:
- REQ-008

Acceptance Criteria:
- AC-015
- AC-016
- AC-017

Dependencies:
- T-004

Allowed Scope:
- src/transit_scholar/api/**
- src/transit_scholar/product/projection.py
- tests/api/**
- tests/product/**
- tests/layer3/**

Forbidden Scope:
- src/transit_scholar/layer3/roles/**
- src/transit_scholar/layer3/memory/**

Implementation Notes:
- Keep internal Trace richness where needed for debugging.
- Introduce sanitized public error/warning projection.
- Replace raw exception strings with stable code plus safe summary.
- Preserve `after_sequence` cursor behavior.

Required Verification:
- python -m pytest tests/api tests/product tests/layer3 -q
- Add tests with fake secret/raw-model text in an internal exception and prove public Timeline excludes it.
- Verify Timeline cursor ordering and next-sequence behavior.


## T-006

Title: Replace Broad Exception Mapping with Typed API Errors

Goal:
Prevent generic `ValueError` and similar exceptions from disguising internal defects as 404/409 responses.

Requirements:
- REQ-009
- REQ-010

Acceptance Criteria:
- AC-018
- AC-019
- AC-020

Dependencies:
- T-001

Allowed Scope:
- src/transit_scholar/api/**
- src/transit_scholar/product/**
- tests/api/**
- tests/product/**

Forbidden Scope:
- src/transit_scholar/layer1/**
- src/transit_scholar/layer2/**
- src/transit_scholar/layer3/roles/**
- src/transit_scholar/layer3/ledger/**

Implementation Notes:
- Add typed Product/application exceptions where current semantics depend on generic exceptions.
- Centralize exception-to-HTTP translation where practical.
- Add a safe catch-all 500 handler.
- Preserve stable existing API error codes where already correct.

Required Verification:
- python -m pytest tests/api tests/product -q
- Add tests for typed 404, 409, 422, 413, and 503 mappings where applicable.
- Inject an unexpected exception and verify HTTP 500 `INTERNAL_ERROR`.
- Verify raw exception detail is absent from client response.


## T-007

Title: Regression-Freeze Paper and File APIs

Goal:
Verify and, only where necessary, correct the existing Paper API surface so it remains compatible with v2 lifecycle/error/composition changes.

Requirements:
- REQ-011
- REQ-012

Acceptance Criteria:
- AC-021
- AC-022
- AC-023
- AC-024

Dependencies:
- T-001
- T-006

Allowed Scope:
- src/transit_scholar/api/routers/papers.py
- src/transit_scholar/api/schemas/**
- src/transit_scholar/product/**
- src/transit_scholar/layer1/**
- tests/api/**
- tests/product/**
- tests/layer1/**

Forbidden Scope:
- src/transit_scholar/layer3/**

Implementation Notes:
- Reuse existing Layer 1 workflows.
- Preserve `PAPER_IN_USE`.
- Preserve safe registered-file lookup.
- Do not redesign ingestion behavior.

Required Verification:
- python -m pytest tests/layer1 tests/product tests/api -q
- Verify valid import.
- Verify oversized upload rejection.
- Verify paper-in-use deletion rejection.
- Verify soft delete/restore.
- Verify arbitrary filesystem path access is unsupported.


## T-008

Title: Regression-Freeze Unified Schema Catalog

Goal:
Verify built-in and user schema discovery, validation, immutability, and downstream resolution through the unified Schema Catalog.

Requirements:
- REQ-013

Acceptance Criteria:
- AC-025
- AC-026

Dependencies:
- T-001
- T-006

Allowed Scope:
- src/transit_scholar/api/routers/schemas.py
- src/transit_scholar/api/schemas/**
- src/transit_scholar/product/**
- src/transit_scholar/layer2/**
- tests/api/**
- tests/product/**
- tests/layer2/**

Forbidden Scope:
- src/transit_scholar/layer3/roles/**
- src/transit_scholar/layer3/runtime/**
- src/transit_scholar/layer3/ledger/**

Implementation Notes:
- Preserve built-in schema behavior.
- Preserve user schema immutability.
- Use the same catalog for Workspace/schema-dependent operations.

Required Verification:
- python -m pytest tests/layer2 tests/product tests/api -q
- Verify built-in and user schemas appear together.
- Verify validation is non-persistent.
- Verify duplicate version creation is rejected.
- Verify a user schema is resolvable during Workspace creation.


## T-009

Title: Regression-Freeze Workspace Lifecycle and Guards

Goal:
Verify Workspace schema immutability, paper membership semantics, schema materialization, and non-terminal AgentRun mutation guards under the new execution lifecycle.

Requirements:
- REQ-014

Acceptance Criteria:
- AC-027
- AC-028
- AC-029

Dependencies:
- T-004
- T-008

Allowed Scope:
- src/transit_scholar/api/routers/workspaces.py
- src/transit_scholar/api/schemas/**
- src/transit_scholar/product/**
- src/transit_scholar/layer2/**
- src/transit_scholar/layer3/workspace/**
- tests/api/**
- tests/product/**
- tests/layer2/**
- tests/layer3/**

Forbidden Scope:
- src/transit_scholar/layer3/roles/**
- src/transit_scholar/layer3/ledger/**
- src/transit_scholar/layer3/memory/**

Implementation Notes:
- `created`, `running`, and `paused` runs remain Workspace-blocking.
- Do not add schema rebinding.
- Removing membership must not delete the library Paper.

Required Verification:
- python -m pytest tests/layer2 tests/layer3 tests/product tests/api -q
- Verify bound and no-schema Workspace creation.
- Verify rebinding is impossible.
- Verify add/remove paper membership.
- Verify `WORKSPACE_BUSY` for created/running/paused runs.


## T-010

Title: Regression-Freeze Conversation and Run HTTP Contract

Goal:
Verify Conversation persistence, non-blocking Turn submission, Run read/control endpoints, and final response persistence after atomic admission and pause changes.

Requirements:
- REQ-005
- REQ-007
- REQ-015

Acceptance Criteria:
- AC-010
- AC-013
- AC-030

Dependencies:
- T-002
- T-004
- T-006
- T-009

Allowed Scope:
- src/transit_scholar/api/routers/conversations.py
- src/transit_scholar/api/routers/runs.py
- src/transit_scholar/api/schemas/**
- src/transit_scholar/product/**
- tests/api/**
- tests/product/**

Forbidden Scope:
- src/transit_scholar/layer1/**
- src/transit_scholar/layer2/**
- src/transit_scholar/layer3/roles/**
- src/transit_scholar/layer3/ledger/**

Implementation Notes:
- Preserve HTTP 202 prompt submission.
- Preserve Turn/Run identity linkage.
- Preserve Conversation context behavior.
- Keep public resume restricted to paused runs.

Required Verification:
- python -m pytest tests/api tests/product -q
- Retain or add two-turn Conversation integration.
- Verify separate AgentRuns per Turn.
- Verify final assistant response persists on the originating Turn.
- Verify pause/resume HTTP state conflicts are stable.


## T-011

Title: Regression-Freeze Final Answer Citation Projection

Goal:
Verify Agent-answer evidence citations remain structured, provenance-backed, and separate from paper bibliography citations.

Requirements:
- REQ-016

Acceptance Criteria:
- AC-031

Dependencies:
- T-005
- T-010

Allowed Scope:
- src/transit_scholar/api/**
- src/transit_scholar/product/**
- tests/api/**
- tests/product/**
- tests/layer3/**

Forbidden Scope:
- src/transit_scholar/layer3/roles/**
- src/transit_scholar/layer3/runtime/**
- src/transit_scholar/layer3/memory/**

Implementation Notes:
- Do not fabricate missing page or quote information.
- Preserve evidence, paper, and ResearchSession provenance.

Required Verification:
- python -m pytest tests/api tests/product tests/layer3 -q
- Verify completed-run citations resolve to persisted evidence/paper identity.
- Verify bibliography and answer-citation DTOs remain distinct.


## T-012

Title: Regression-Freeze Wiki API

Goal:
Verify Base Wiki, Agentic Wiki, structured page/entity reads, unified search, and no-schema unsupported behavior remain correct after v2 composition changes.

Requirements:
- REQ-017

Acceptance Criteria:
- AC-032
- AC-033

Dependencies:
- T-008
- T-009

Allowed Scope:
- src/transit_scholar/api/routers/wiki.py
- src/transit_scholar/api/schemas/**
- src/transit_scholar/product/**
- src/transit_scholar/layer3/agentic_wiki/**
- src/transit_scholar/layer3/knowledge/**
- tests/api/**
- tests/product/**
- tests/layer3/**

Forbidden Scope:
- src/transit_scholar/layer3/roles/**
- src/transit_scholar/layer3/runtime/**
- src/transit_scholar/layer3/ledger/**

Implementation Notes:
- Keep structured JSON canonical.
- Preserve `source_kind`.
- Do not introduce backend HTML rendering.

Required Verification:
- python -m pytest tests/api tests/product tests/layer3 -q
- Verify schema-bound Wiki status/build/read/search.
- Verify no-schema Base Wiki unsupported.
- Verify Base Wiki and Agentic Wiki search hits preserve source kind.


## T-013

Title: Harden Startup, Shutdown, and Capability Reporting

Goal:
Finalize process lifecycle behavior for interrupted-run reconciliation, graceful execution-manager shutdown, and runtime-aware capability reporting.

Requirements:
- REQ-018
- REQ-019
- REQ-020

Acceptance Criteria:
- AC-034
- AC-035
- AC-036

Dependencies:
- T-001
- T-003
- T-004
- T-006

Allowed Scope:
- src/transit_scholar/api/app.py
- src/transit_scholar/api/runtime/**
- src/transit_scholar/api/dependencies.py
- src/transit_scholar/product/**
- tests/api/**
- tests/product/**

Forbidden Scope:
- src/transit_scholar/layer1/**
- src/transit_scholar/layer2/**
- src/transit_scholar/layer3/roles/**
- src/transit_scholar/layer3/ledger/**
- src/transit_scholar/layer3/memory/**

Implementation Notes:
- Reconcile unowned running runs to paused at startup.
- Do not auto-resume.
- On shutdown, request cooperative pause for active execution where possible before teardown.
- Capabilities must reflect actual provider/runtime availability rather than static booleans.

Required Verification:
- python -m pytest tests/api tests/product -q
- Simulate restart with persisted running run and verify paused/no auto-resume.
- Simulate shutdown with active run and verify cleanup/pause behavior.
- Verify capabilities when provider is configured.
- Verify capabilities when provider/runtime is unavailable but library/schema features remain usable.


## T-014

Title: API Layer v2 Freeze Gate and End-to-End Acceptance

Goal:
Run the complete regression and integration gate required to declare the API Layer freeze-ready after all v2 corrections.

Requirements:
- REQ-001
- REQ-002
- REQ-003
- REQ-004
- REQ-005
- REQ-006
- REQ-007
- REQ-008
- REQ-009
- REQ-010
- REQ-011
- REQ-012
- REQ-013
- REQ-014
- REQ-015
- REQ-016
- REQ-017
- REQ-018
- REQ-019
- REQ-020
- REQ-021

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
- AC-013
- AC-014
- AC-015
- AC-016
- AC-017
- AC-018
- AC-019
- AC-020
- AC-021
- AC-022
- AC-023
- AC-024
- AC-025
- AC-026
- AC-027
- AC-028
- AC-029
- AC-030
- AC-031
- AC-032
- AC-033
- AC-034
- AC-035
- AC-036
- AC-037
- AC-038

Dependencies:
- T-005
- T-007
- T-008
- T-009
- T-010
- T-011
- T-012
- T-013

Allowed Scope:
- src/transit_scholar/api/**
- src/transit_scholar/product/**
- tests/api/**
- tests/product/**
- tests/layer1/**
- tests/layer2/**
- tests/layer3/**

Forbidden Scope:
- None

Implementation Notes:
- This is a freeze/verification task, not a feature-expansion task.
- Fix only defects required to satisfy this Contract.
- Do not add UI Layer behavior.
- Do not add new API domains beyond the frozen API surface.
- Include a real local API smoke using representative PDF data when available in repository test fixtures or accepted local smoke inputs.

Required Verification:
- python -m pytest tests/api tests/product tests/layer1 tests/layer2 tests/layer3 -q
- Verify OpenAPI generation for `/api/v1/`.
- Verify atomic concurrent prompt admission with no orphan state.
- Verify application-scope bootstrap occurs once and request/worker Sessions are isolated.
- Verify active-session cooperative pause and same-run resume.
- Verify Timeline sanitization and cursor semantics.
- Verify typed 404/409/500 mappings.
- Verify Paper import/delete/restore/citation behavior.
- Verify Schema Catalog and immutable Workspace binding.
- Verify Workspace busy guards.
- Verify Conversation two-turn flow.
- Verify final answer evidence citations.
- Verify Base Wiki/Agentic Wiki structured read/search.
- Verify startup reconciliation.
- Verify graceful shutdown cleanup.
- Verify capabilities reflect runtime/provider availability.
- Run the end-to-end local API smoke: PDF import -> schema/workspace setup -> conversation -> prompt submission -> run/timeline -> pause/resume -> final answer/citations -> wiki read/search.
