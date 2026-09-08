# Acceptance Criteria

## AC-001

Requirements:
- REQ-001
- REQ-021

Criterion:
When the formal API application starts, all product endpoints MUST be served under `/api/v1/`, a valid OpenAPI document MUST be generated, and response models MUST be API-layer DTOs rather than direct Product/Core model classes.


## AC-002

Requirements:
- REQ-001

Criterion:
When an API router performs a research operation, it MUST call Product Layer or formal service boundaries and MUST NOT directly implement Agent loop logic, evidence mutation, claim mutation, role execution, or memory mutation.


## AC-003

Requirements:
- REQ-002

Criterion:
During one API process lifetime, application startup MUST initialize the shared engine/session factory, RuntimeFactory, SchemaCatalog, and configured provider/runtime dependencies once. Two ordinary HTTP requests MUST NOT each invoke the full product bootstrap/migration path.


## AC-004

Requirements:
- REQ-002

Criterion:
When two independent HTTP requests access Product functionality, they MUST use different SQLAlchemy Session instances. Closing one request scope MUST NOT invalidate the other request.


## AC-005

Requirements:
- REQ-002
- REQ-004

Criterion:
When a background AgentRun executes after the originating HTTP request has returned, the worker MUST use a fresh worker-owned Product/session scope while reusing eligible process-scope dependencies.


## AC-006

Requirements:
- REQ-003
- REQ-004

Criterion:
When two prompt submissions race for the single execution slot, exactly one request MAY acquire admission. The rejected request MUST return HTTP 409 with `RUNNER_BUSY` before creating a ConversationTurn or AgentRun.


## AC-007

Requirements:
- REQ-003

Criterion:
After a rejected `RUNNER_BUSY` prompt submission, database state MUST contain no additional orphan Turn and no new `created`, `running`, or `paused` AgentRun associated with the rejected request.


## AC-008

Requirements:
- REQ-004

Criterion:
If executor submission raises after the execution slot has been reserved, the manager MUST release the slot so a later valid submission can proceed.


## AC-009

Requirements:
- REQ-004

Criterion:
When an AgentRun Future reaches a terminal state, the execution manager MUST clear active ownership and MUST not retain unbounded completed Future references.


## AC-010

Requirements:
- REQ-005
- REQ-015

Criterion:
When a valid prompt is submitted, the API MUST return HTTP 202 containing `turn_id` and `agent_run_id` before the AgentRun completes, and the persisted Turn MUST later contain the final assistant response when execution succeeds.


## AC-011

Requirements:
- REQ-006
- REQ-007

Criterion:
When pause is requested during an active ResearchSession, the pause signal MUST be observable within the session/runtime path and the run MUST reach `paused` at the next supported safe checkpoint without waiting for the entire multi-step ResearchSession to finish solely because pause is only checked between sessions.


## AC-012

Requirements:
- REQ-006

Criterion:
When pause is requested while an indivisible provider call or equivalent external operation is in flight, the operation MAY complete, but the runtime MUST persist state and pause at the next safe checkpoint. It MUST NOT mark the run cancelled as a substitute for pause.


## AC-013

Requirements:
- REQ-007

Criterion:
When resume is requested for a paused run, execution MUST continue the same AgentRun from persisted checkpoint state. Resume requests for terminal runs MUST return HTTP 409 and MUST NOT mutate run state.


## AC-014

Requirements:
- REQ-007

Criterion:
While a pause request is pending, the API MAY report a derived display value such as `pausing`, but persisted Core status MUST remain an existing lifecycle value and no new authoritative `pausing` status may be stored.


## AC-015

Requirements:
- REQ-008

Criterion:
When `GET /api/v1/runs/{run_id}/timeline?after_sequence=N` is called, every returned event MUST have sequence greater than `N`, events MUST be ascending by sequence, and the response MUST return the highest emitted sequence as the next cursor.


## AC-016

Requirements:
- REQ-008

Criterion:
Timeline output MAY contain normalized planning, research-session, query, retrieval, evidence, claim, synthesis, status, warning, and error events, but MUST NOT contain hidden chain-of-thought, hidden prompts, private scratchpads, raw provider output, or raw exception text copied directly from `str(exc)`.


## AC-017

Requirements:
- REQ-008

Criterion:
When an internal provider or structured-output failure is represented in the public Timeline, the event MUST expose a sanitized stable code and user-safe summary rather than provider-private or internal exception detail.


## AC-018

Requirements:
- REQ-009
- REQ-010

Criterion:
When a known typed domain/application error occurs, it MUST map to its defined HTTP status and stable error code. Broad generic exception catches MUST NOT remap unrelated programming/runtime failures to 404 or 409.


## AC-019

Requirements:
- REQ-009
- REQ-010

Criterion:
When an unexpected exception reaches the API boundary, the response MUST be HTTP 500 with code `INTERNAL_ERROR` or an equivalent frozen internal-error code, and internal exception detail MUST not be exposed in the client message.


## AC-020

Requirements:
- REQ-010

Criterion:
Request validation failures MUST return HTTP 422 using the stable error envelope; oversized PDF uploads MUST return HTTP 413; missing resources MUST return HTTP 404; state conflicts MUST return HTTP 409.


## AC-021

Requirements:
- REQ-011

Criterion:
When a valid PDF within the configured size limit is imported, the existing ingestion workflow MUST run and the API MUST return persisted paper identity plus processing/readiness state.


## AC-022

Requirements:
- REQ-011

Criterion:
When manual metadata correction, reconcile, enrichment refresh, duplicate adjudication, bibliography retrieval, soft delete, or restore is requested, the API MUST reuse the corresponding existing Layer 1/Product workflow rather than reimplementing the domain mutation in the router.


## AC-023

Requirements:
- REQ-011

Criterion:
When library deletion is requested for a paper still belonging to an active Workspace, the API MUST return HTTP 409 with `PAPER_IN_USE` and MUST NOT soft-delete the paper or files.


## AC-024

Requirements:
- REQ-012

Criterion:
When a registered PDF file is requested by file identity, the API MUST resolve the file through persisted metadata and return the associated PDF. No endpoint parameter may provide arbitrary filesystem path traversal.


## AC-025

Requirements:
- REQ-013

Criterion:
Schema listing MUST return built-in and persisted user schema versions through one catalog abstraction. A valid user-created schema version MUST become resolvable by Workspace creation and later schema-dependent operations.


## AC-026

Requirements:
- REQ-013

Criterion:
Schema draft validation MUST not persist the draft. Creating an already existing schema ID/version or attempting in-place modification of an existing version MUST be rejected.


## AC-027

Requirements:
- REQ-014

Criterion:
Workspace creation without a schema MUST persist schema mode `none`. Workspace creation with a schema MUST persist schema ID, version, and hash, and no API endpoint may rebind the Workspace later.


## AC-028

Requirements:
- REQ-014

Criterion:
When a Workspace has a `created`, `running`, or `paused` AgentRun depending on its revision, paper membership changes, archive, and delete MUST return HTTP 409 with `WORKSPACE_BUSY` and MUST not mutate the Workspace.


## AC-029

Requirements:
- REQ-014

Criterion:
Removing a paper from a Workspace MUST change Workspace membership only and MUST leave the global paper active in the library.


## AC-030

Requirements:
- REQ-015

Criterion:
Conversation creation/list/read and Turn read MUST preserve Workspace association, ordered Turn sequence, user message, resolved user goal, AgentRun identity, status, and completed assistant response.


## AC-031

Requirements:
- REQ-016

Criterion:
A completed Turn with supporting evidence MUST expose structured Agent-answer citations that identify evidence and paper provenance plus available page/source locator information. These DTOs MUST remain distinct from paper bibliography citation DTOs.


## AC-032

Requirements:
- REQ-017

Criterion:
A schema-bound Workspace MUST expose Wiki overview/status, Base Wiki build, structured pages/entities, Agentic Wiki entries, and unified search. Search results MUST preserve `source_kind`.


## AC-033

Requirements:
- REQ-017

Criterion:
For a no-schema Workspace, Base Wiki status MUST be represented as unsupported rather than returning an internal server error.


## AC-034

Requirements:
- REQ-018

Criterion:
At API startup, a persisted run marked `running` with no current-process worker ownership MUST transition to `paused`, MUST retain resumable state, and MUST NOT execute automatically.


## AC-035

Requirements:
- REQ-019

Criterion:
When application shutdown occurs with an active run, the shutdown path MUST request or honor cooperative pause/checkpoint behavior before executor teardown where possible, MUST release execution-manager resources, and MUST not leave active-run ownership permanently set after process teardown.


## AC-036

Requirements:
- REQ-020

Criterion:
When the required Agent runtime/provider is unavailable, `/api/v1/capabilities` MUST NOT advertise executable Agent capability as available merely because routes exist. Schema/library capabilities MAY remain available.


## AC-037

Requirements:
- REQ-021

Criterion:
When internal Product/Core models gain fields that are not part of API DTOs, the serialized HTTP response and generated OpenAPI schema MUST remain unchanged until the API DTO is explicitly revised.


## AC-038

Requirements:
- REQ-001
- REQ-003
- REQ-006
- REQ-008
- REQ-009
- REQ-011
- REQ-013
- REQ-014
- REQ-015
- REQ-016
- REQ-017
- REQ-018
- REQ-019
- REQ-020
- REQ-021

Criterion:
Before API Layer freeze, the full regression suite and API integration suite MUST pass, including an end-to-end local API smoke covering PDF import, schema/workspace setup, Conversation prompt submission, non-blocking AgentRun execution, Timeline observation, cooperative pause/resume, final answer citation retrieval, Wiki read/search behavior, and restart reconciliation.
