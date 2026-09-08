# Task Breakdown

## T-001

Title: Build Formal API Foundation

Goal:
Create the versioned FastAPI application foundation, explicit API DTO boundary, request-scoped dependency model, standard error envelope, health endpoint, capability endpoint, and OpenAPI contract under `/api/v1/`.

Requirements:
- REQ-001
- REQ-002
- REQ-017
- REQ-018
- REQ-020

Acceptance Criteria:
- AC-001
- AC-002
- AC-003
- AC-004
- AC-033
- AC-034
- AC-035
- AC-038

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
- Use FastAPI.
- Mount formal product routes under `/api/v1/`.
- Define explicit API request/response DTOs.
- Implement request-scoped database/Product dependencies.
- Reuse thread-safe process-level dependencies only.
- Do not remove the existing acceptance-panel routes during this task.

Required Verification:
- python -m pytest tests/api -q
- python -m pytest tests/product -q
- Verify the generated FastAPI OpenAPI document contains `/api/v1/health` and `/api/v1/capabilities`.


## T-002

Title: Split Prompt Preparation from AgentRun Execution

Goal:
Add a Product Layer operation that creates and persists the Conversation Turn and AgentRun, resolves the user goal, links Turn and Run, and returns their identities without executing the AgentRun. Preserve the existing synchronous convenience flow by composing preparation and execution.

Requirements:
- REQ-010
- REQ-011

Acceptance Criteria:
- AC-020
- AC-021
- AC-022

Dependencies:
- None

Allowed Scope:
- src/transit_scholar/product/**
- tests/product/**
- tests/api/**

Forbidden Scope:
- src/transit_scholar/layer1/**
- src/transit_scholar/layer2/**
- src/transit_scholar/layer3/roles/**
- src/transit_scholar/layer3/retrieval/**

Implementation Notes:
- Add a formal `prepare_message` or equivalent Product operation.
- Preparation must commit Turn and AgentRun identities before execution scheduling.
- Preserve the existing `submit_message` behavior as a synchronous convenience path built from preparation plus execution.
- Do not duplicate conversation context resolution logic.

Required Verification:
- python -m pytest tests/product -q
- Add and run tests proving preparation returns persisted `turn_id` and `agent_run_id` before run completion.
- Add and run a regression test proving existing synchronous submit behavior still produces the completed Conversation Turn.


## T-003

Title: Implement Local Single-Run Execution Manager

Goal:
Implement the local background AgentRun execution manager used by API prompt submission, enforcing one active execution at a time and ensuring the worker uses its own persistence/runtime scope.

Requirements:
- REQ-011
- REQ-012
- REQ-018

Acceptance Criteria:
- AC-021
- AC-023
- AC-036

Dependencies:
- T-001
- T-002

Allowed Scope:
- src/transit_scholar/api/runtime/**
- src/transit_scholar/api/**
- src/transit_scholar/product/**
- tests/api/**
- tests/product/**

Forbidden Scope:
- src/transit_scholar/layer1/**
- src/transit_scholar/layer2/**
- src/transit_scholar/layer3/ledger/**
- src/transit_scholar/layer3/memory/**

Implementation Notes:
- Use a local single-worker implementation.
- The execution manager should accept persisted AgentRun identity, not request-owned SQLAlchemy Session objects.
- A second active execution request must produce the API conflict code `RUNNER_BUSY`.
- Do not add an external broker or distributed worker dependency.

Required Verification:
- python -m pytest tests/api -q
- Add and run a concurrency regression proving only one AgentRun can execute at a time.
- Add and run a session-lifecycle regression proving the worker remains valid after the originating request scope closes.


## T-004

Title: Add Cooperative Pause and Resume Control

Goal:
Connect Product/API run-control operations to the existing recoverable Agent runtime so a running AgentRun can pause safely at a checkpoint and later resume from persisted state.

Requirements:
- REQ-013

Acceptance Criteria:
- AC-024
- AC-025
- AC-026

Dependencies:
- T-003

Allowed Scope:
- src/transit_scholar/api/runtime/**
- src/transit_scholar/api/routers/**
- src/transit_scholar/api/schemas/**
- src/transit_scholar/product/**
- src/transit_scholar/layer3/runtime/**
- tests/api/**
- tests/product/**
- tests/layer3/**

Forbidden Scope:
- src/transit_scholar/layer3/roles/**
- src/transit_scholar/layer3/ledger/**
- src/transit_scholar/layer3/memory/**
- src/transit_scholar/layer3/knowledge_evolution/**

Implementation Notes:
- Introduce a cooperative pause signal or equivalent formal run-control seam.
- Observe the signal only at safe runtime checkpoints.
- Persist recoverable state before transitioning to `paused`.
- Resume must continue the same AgentRun.
- A pending pause may be exposed through a derived API flag or display field.
- Do not persist a new Core lifecycle state named `pausing`.

Required Verification:
- python -m pytest tests/layer3 tests/product tests/api -q
- Add and run a test where pause is requested during execution and the run reaches persisted `paused` state.
- Add and run a test proving resume continues the same AgentRun from checkpoint.
- Add and run a test proving terminal-run resume is rejected without state mutation.


## T-005

Title: Expose Paper Library and File APIs

Goal:
Expose formal `/api/v1/` paper import, listing, detail, metadata, reconciliation, enrichment, duplicate, bibliography, file retrieval, soft-delete, and restore APIs by adapting existing Layer 1 services.

Requirements:
- REQ-003
- REQ-004
- REQ-005

Acceptance Criteria:
- AC-005
- AC-006
- AC-007
- AC-008
- AC-009
- AC-010
- AC-011
- AC-034

Dependencies:
- T-001

Allowed Scope:
- src/transit_scholar/api/routers/**
- src/transit_scholar/api/schemas/**
- src/transit_scholar/api/adapters/**
- src/transit_scholar/product/**
- src/transit_scholar/layer1/**
- tests/api/**
- tests/layer1/**
- tests/product/**

Forbidden Scope:
- src/transit_scholar/layer3/**

Implementation Notes:
- Reuse existing ingestion and maintenance workflows.
- Preserve the configured PDF upload-size limit.
- Add a Product-level library-delete guard that checks active Workspace membership before soft deletion.
- Keep Workspace removal and library deletion as separate operations.
- Resolve PDF file content from registered file identity only.
- Preserve existing duplicate adjudication decisions.

Required Verification:
- python -m pytest tests/layer1 tests/product tests/api -q
- Add and run tests for valid PDF import and oversized upload rejection.
- Add and run tests for `PAPER_IN_USE`.
- Add and run tests for soft-delete and restore.
- Add and run tests proving arbitrary filesystem path retrieval is not part of the file endpoint contract.


## T-006

Title: Build Unified Immutable Schema Catalog

Goal:
Create a unified Schema Catalog that exposes built-in and user-created schemas, supports validation and immutable user schema creation, and is used consistently by downstream Workspace/schema operations.

Requirements:
- REQ-006

Acceptance Criteria:
- AC-012
- AC-013
- AC-014

Dependencies:
- T-001

Allowed Scope:
- src/transit_scholar/api/routers/**
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
- Preserve existing built-in schema discovery.
- Add a user-schema store under the application's existing local data root or an equivalent existing storage abstraction.
- The same catalog resolver must be used by Workspace creation and later schema-dependent services.
- Schema validation must not persist drafts.
- Reject in-place modification of existing schema versions.

Required Verification:
- python -m pytest tests/layer2 tests/product tests/api -q
- Add and run tests showing built-in and user schemas appear in one catalog.
- Add and run tests showing validation is non-persistent.
- Add and run tests rejecting duplicate schema ID/version creation.
- Add and run an integration test proving a newly created user schema can be resolved for Workspace creation.


## T-007

Title: Expose Workspace and Workspace Schema APIs

Goal:
Expose formal Workspace lifecycle, immutable schema binding, paper membership, schema readiness, and schema materialization APIs with non-terminal-run mutation guards.

Requirements:
- REQ-007
- REQ-008
- REQ-009

Acceptance Criteria:
- AC-015
- AC-016
- AC-017
- AC-018
- AC-019

Dependencies:
- T-001
- T-006

Allowed Scope:
- src/transit_scholar/api/routers/**
- src/transit_scholar/api/schemas/**
- src/transit_scholar/api/adapters/**
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
- Preserve existing Workspace revision semantics.
- Preserve immutable schema binding semantics.
- Add a Product/API guard that rejects membership, archive, and delete mutations while the Workspace has a non-terminal AgentRun.
- Removing a paper from a Workspace must only alter membership.
- Use the unified Schema Catalog from T-006 for schema resolution.

Required Verification:
- python -m pytest tests/layer2 tests/layer3 tests/product tests/api -q
- Add and run tests for schema-bound and no-schema Workspace creation.
- Add and run a test proving schema rebinding is impossible through the API.
- Add and run tests for paper add/remove membership.
- Add and run tests for `WORKSPACE_BUSY`.
- Add and run tests for schema materialization through the formal API path.


## T-008

Title: Expose Conversation and Prompt Submission APIs

Goal:
Expose Conversation list/create/read, Turn read, and non-blocking prompt submission endpoints that use Product Layer conversation semantics and LocalRunManager scheduling.

Requirements:
- REQ-010
- REQ-011
- REQ-012

Acceptance Criteria:
- AC-020
- AC-021
- AC-022
- AC-023

Dependencies:
- T-002
- T-003
- T-007

Allowed Scope:
- src/transit_scholar/api/routers/**
- src/transit_scholar/api/schemas/**
- src/transit_scholar/api/adapters/**
- src/transit_scholar/product/**
- tests/api/**
- tests/product/**

Forbidden Scope:
- src/transit_scholar/layer1/**
- src/transit_scholar/layer2/**
- src/transit_scholar/layer3/roles/**
- src/transit_scholar/layer3/ledger/**

Implementation Notes:
- Prompt submission must return HTTP 202 with `turn_id` and `agent_run_id`.
- Do not wait for AgentRun completion in the submission endpoint.
- Conversation read responses must include persisted Turn history and completed assistant responses.
- Do not duplicate conversation goal-resolution logic inside API routers.

Required Verification:
- python -m pytest tests/product tests/api -q
- Add and run an HTTP integration test for Conversation creation and prompt submission.
- Add and run a two-turn Conversation test proving separate AgentRuns are created while Conversation history remains continuous.
- Add and run a test proving completed final response persists on the original Turn.


## T-009

Title: Expose Run State and Incremental Timeline APIs

Goal:
Expose AgentRun state, pause/resume commands, and incremental UI-safe Timeline projection derived from persisted trace and formal research artifacts.

Requirements:
- REQ-013
- REQ-014

Acceptance Criteria:
- AC-024
- AC-025
- AC-026
- AC-027
- AC-028

Dependencies:
- T-004
- T-008

Allowed Scope:
- src/transit_scholar/api/routers/**
- src/transit_scholar/api/schemas/**
- src/transit_scholar/api/adapters/**
- src/transit_scholar/api/runtime/**
- src/transit_scholar/product/**
- tests/api/**
- tests/product/**
- tests/layer3/**

Forbidden Scope:
- src/transit_scholar/layer3/roles/**
- src/transit_scholar/layer3/memory/**
- src/transit_scholar/layer3/knowledge_evolution/**

Implementation Notes:
- Use persisted Trace sequence as the timeline cursor where possible.
- Implement `after_sequence`.
- Normalize internal events to the approved timeline kinds.
- Do not expose raw internal Trace payloads by default.
- Do not expose hidden reasoning or private prompts.
- Run state responses may include derived `pause_requested` or `display_status`.

Required Verification:
- python -m pytest tests/layer3 tests/product tests/api -q
- Add and run tests proving timeline ordering and `after_sequence` behavior.
- Add and run tests proving only events after the cursor are returned.
- Add and run tests verifying forbidden hidden reasoning fields are absent from Timeline DTOs.
- Add and run HTTP tests for pause and resume endpoints.


## T-010

Title: Build Final Answer Citation API Projection

Goal:
Convert completed AgentRun/Turn final responses and admitted evidence provenance into stable API citation DTOs while keeping paper bibliography citations separate.

Requirements:
- REQ-015

Acceptance Criteria:
- AC-029

Dependencies:
- T-008
- T-009

Allowed Scope:
- src/transit_scholar/api/adapters/**
- src/transit_scholar/api/schemas/**
- src/transit_scholar/api/routers/**
- src/transit_scholar/product/**
- tests/api/**
- tests/product/**
- tests/layer3/**

Forbidden Scope:
- src/transit_scholar/layer3/roles/**
- src/transit_scholar/layer3/runtime/**
- src/transit_scholar/layer3/memory/**

Implementation Notes:
- Preserve paper identity, evidence identity, ResearchSession identity, and available page/source locators.
- Include evidence quote only when it is part of the formal admitted evidence/provenance data.
- Do not infer or fabricate missing page or quote information.
- Do not reuse the paper bibliography response model for Agent answer citations.

Required Verification:
- python -m pytest tests/layer3 tests/product tests/api -q
- Add and run a completed-run test proving final answer citations resolve to persisted evidence and paper identities.
- Add and run a regression proving paper bibliography citations and answer evidence citations serialize through different DTOs.


## T-011

Title: Expose Structured Base Wiki and Agentic Wiki APIs

Goal:
Expose Workspace Wiki overview/status, Base Wiki build, pages, entities, Agentic Wiki entries, and unified search as structured API data.

Requirements:
- REQ-016

Acceptance Criteria:
- AC-030
- AC-031
- AC-032

Dependencies:
- T-001
- T-006
- T-007

Allowed Scope:
- src/transit_scholar/api/routers/**
- src/transit_scholar/api/schemas/**
- src/transit_scholar/api/adapters/**
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
- Reuse existing Workspace Wiki services.
- Preserve Base Wiki lifecycle/status vocabulary.
- Return Base Wiki unsupported for no-schema Workspaces.
- Preserve `source_kind` for unified search.
- Return structured data rather than canonical backend HTML.

Required Verification:
- python -m pytest tests/layer3 tests/product tests/api -q
- Add and run tests for schema-bound Wiki status/build/read.
- Add and run a no-schema test returning Base Wiki unsupported.
- Add and run unified search tests containing Base Wiki and Agentic Wiki hits with preserved `source_kind`.


## T-012

Title: Implement Startup Reconciliation for Interrupted Runs

Goal:
On API startup, reconcile persisted AgentRuns that are marked running but have no active worker in the current process into resumable paused state without automatic execution.

Requirements:
- REQ-019

Acceptance Criteria:
- AC-037

Dependencies:
- T-003
- T-004

Allowed Scope:
- src/transit_scholar/api/app.py
- src/transit_scholar/api/runtime/**
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
- Perform reconciliation during API lifespan/startup.
- Only runs with no current-process worker ownership should be reconciled.
- Do not automatically resume LLM execution.
- Preserve existing durable runtime checkpoints for later explicit resume.

Required Verification:
- python -m pytest tests/product tests/api -q
- Add and run a restart simulation with a persisted `running` AgentRun and no active worker.
- Verify startup transitions the run to `paused`.
- Verify no automatic Agent execution occurs.
- Verify explicit resume remains possible afterward.


## T-013

Title: Freeze Error Mapping and API Contract Regression

Goal:
Complete cross-domain HTTP error mappings, verify DTO isolation, validate the complete OpenAPI surface, and add regression tests covering the formal `/api/v1/` contract.

Requirements:
- REQ-001
- REQ-017
- REQ-020

Acceptance Criteria:
- AC-001
- AC-033
- AC-034
- AC-038

Dependencies:
- T-005
- T-006
- T-007
- T-008
- T-009
- T-010
- T-011
- T-012

Allowed Scope:
- src/transit_scholar/api/**
- src/transit_scholar/product/**
- tests/api/**
- tests/product/**

Forbidden Scope:
- src/transit_scholar/layer1/**
- src/transit_scholar/layer2/**
- src/transit_scholar/layer3/**

Implementation Notes:
- Freeze stable error codes for known conflicts and not-found conditions.
- Verify OpenAPI contains every endpoint defined by this Contract.
- Verify internal Product/Core-only fields do not leak through response DTOs.
- Preserve the legacy acceptance panel until the formal API test suite is stable.

Required Verification:
- python -m pytest tests/api tests/product -q
- Export and validate the FastAPI OpenAPI schema.
- Verify all required `/api/v1/` endpoints from this Contract exist.
- Verify known conflict scenarios return HTTP 409 and structured error envelopes.
- Verify invalid request bodies return HTTP 422.
- Verify missing resources return HTTP 404.


## T-014

Title: Execute Full API Layer Integration Acceptance

Goal:
Validate the complete formal API Layer across paper ingestion, schema, workspace, conversation, AgentRun execution, timeline, pause/resume, citations, wiki, deletion guards, and restart reconciliation.

Requirements:
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
- REQ-018
- REQ-019

Acceptance Criteria:
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
- AC-035
- AC-036
- AC-037

Dependencies:
- T-013

Allowed Scope:
- tests/api/**
- tests/product/**
- tests/layer1/**
- tests/layer2/**
- tests/layer3/**
- src/transit_scholar/api/**

Forbidden Scope:
- None

Implementation Notes:
- This task is primarily integration and regression verification.
- Only implementation changes required to satisfy failed Contract criteria should be made.
- Do not introduce new API capabilities beyond this Contract.
- Include at least one end-to-end API test that creates a Conversation Turn, receives an AgentRun ID before completion, observes Timeline events, reaches final answer persistence, and validates evidence citation projection.

Required Verification:
- python -m pytest tests/api tests/product tests/layer1 tests/layer2 tests/layer3 -q
- Verify a valid PDF can be imported and retrieved through formal API endpoints.
- Verify user schema creation and immutable Workspace binding.
- Verify Workspace mutation guards during non-terminal AgentRun state.
- Verify non-blocking prompt submission and single-run execution admission.
- Verify Timeline incremental cursor semantics.
- Verify cooperative pause and explicit resume.
- Verify final answer citation provenance.
- Verify no-schema Base Wiki unsupported behavior.
- Verify Base Wiki and Agentic Wiki structured retrieval and unified search.
- Verify paper-in-use deletion rejection.
- Verify interrupted-run startup reconciliation.
