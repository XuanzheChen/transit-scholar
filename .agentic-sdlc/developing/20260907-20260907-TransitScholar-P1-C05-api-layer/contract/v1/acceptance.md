# Acceptance Criteria

## AC-001

Requirements:
- REQ-001
- REQ-020

Criterion:
When the API application starts, all formal product endpoints MUST be served under `/api/v1/`, FastAPI MUST expose a valid OpenAPI document for those endpoints, and API response schemas MUST be explicit API-layer DTOs rather than direct Product/Core model exposure.


## AC-002

Requirements:
- REQ-001

Criterion:
When an API router performs a product operation, the router MUST call an existing Product Layer or formal service boundary and MUST NOT directly implement Agent orchestration, evidence mutation, claim mutation, memory mutation, or role execution logic.


## AC-003

Requirements:
- REQ-002

Criterion:
When `GET /api/v1/health` is called while the local API is operational, the endpoint MUST return HTTP 200 and a machine-readable healthy status without exposing secrets or unnecessary local filesystem/database connection details.


## AC-004

Requirements:
- REQ-002

Criterion:
When `GET /api/v1/capabilities` is called, the response MUST include the enabled/disabled state of pause/resume, user schema creation, Base Wiki, Agentic Wiki, semantic wiki search, and the configured PDF upload size limit.


## AC-005

Requirements:
- REQ-003

Criterion:
When a valid PDF within the configured upload limit is submitted to `POST /api/v1/papers/import`, the system MUST execute the existing ingestion workflow and return a persisted paper identity plus processing/readiness state.


## AC-006

Requirements:
- REQ-003

Criterion:
When paper list and paper detail endpoints are called for persisted papers, the API MUST return paper metadata and processing state without requiring direct database access by the caller.


## AC-007

Requirements:
- REQ-004

Criterion:
When valid manual paper metadata is submitted, the API MUST use the existing metadata correction workflow and MUST NOT bypass the existing metadata candidate/selection semantics.


## AC-008

Requirements:
- REQ-004

Criterion:
When duplicate relations exist for a paper, the API MUST expose them and MUST accept only supported duplicate adjudication decisions through the formal duplicate resolution workflow.


## AC-009

Requirements:
- REQ-004

Criterion:
When library-level deletion is requested for a paper that is still a member of at least one active Workspace, the API MUST return HTTP 409 with a stable `PAPER_IN_USE` error and MUST NOT soft-delete the paper or its files.


## AC-010

Requirements:
- REQ-004

Criterion:
When library-level deletion is requested for a paper that is not used by any active Workspace, the existing soft-delete behavior MUST be invoked; when restore is requested for that deleted paper, the existing restore behavior MUST be invoked.


## AC-011

Requirements:
- REQ-005

Criterion:
When a registered paper file is requested by file identity, the API MUST return the associated PDF content and MUST resolve its storage location from persisted file metadata. Supplying an arbitrary filesystem path MUST NOT be supported by the endpoint contract.


## AC-012

Requirements:
- REQ-006

Criterion:
When schema catalog listing is requested, both built-in schema versions and persisted user-created schema versions MUST be returned through one catalog interface.


## AC-013

Requirements:
- REQ-006

Criterion:
When a schema draft is submitted to the validation endpoint, validation MUST NOT persist the draft. Invalid drafts MUST return structured validation issues; valid drafts MUST report success.


## AC-014

Requirements:
- REQ-006

Criterion:
When a valid new user schema version is created, it MUST become resolvable through the same schema catalog used by Workspace creation and schema materialization. An attempt to create an already existing schema ID/version pair or modify an existing version in place MUST be rejected.


## AC-015

Requirements:
- REQ-007

Criterion:
When a Workspace is created without a schema, its persisted schema mode MUST be `none`. When created with a schema, the persisted binding MUST contain the selected schema ID, version, and hash. No API endpoint may subsequently rebind that Workspace to another schema.


## AC-016

Requirements:
- REQ-007

Criterion:
When Workspace list, detail, archive, and delete operations are invoked in valid states, they MUST use existing Workspace lifecycle services and preserve the rule that Workspace deletion does not delete global Paper records.


## AC-017

Requirements:
- REQ-008

Criterion:
When papers are added to or removed from a Workspace in an allowed state, Workspace membership MUST be updated through the existing Workspace service. Removing membership MUST leave the global Paper active and available in the library.


## AC-018

Requirements:
- REQ-008

Criterion:
When a Workspace has a non-terminal AgentRun in `created`, `running`, or `paused` state, attempts to add/remove paper membership, archive the Workspace, or delete the Workspace MUST return HTTP 409 with a stable `WORKSPACE_BUSY` error and MUST NOT mutate the Workspace.


## AC-019

Requirements:
- REQ-009

Criterion:
When schema readiness or materialization is requested for a schema-bound Workspace and member paper, the API MUST use the existing Workspace schema services and MUST enforce active Workspace, membership, and immutable binding checks.


## AC-020

Requirements:
- REQ-010

Criterion:
When a Conversation is created for an active Workspace and later retrieved, the persisted Conversation MUST be associated with that Workspace and its Turn history MUST preserve user message, resolved user goal, AgentRun identity, status, and completed assistant response.


## AC-021

Requirements:
- REQ-011

Criterion:
When a valid prompt is submitted to `POST /api/v1/conversations/{conversation_id}/turns`, the API MUST persist the new Turn and AgentRun, schedule the run for execution, and return HTTP 202 containing both `turn_id` and `agent_run_id` before the AgentRun completes.


## AC-022

Requirements:
- REQ-011
- REQ-010

Criterion:
When an AgentRun created from a Turn completes successfully, the final response MUST be persisted on that same Conversation Turn and remain retrievable through Conversation or Turn read endpoints.


## AC-023

Requirements:
- REQ-012

Criterion:
When one AgentRun is actively executing and a second execution is requested, the second request MUST be rejected with HTTP 409 and stable error code `RUNNER_BUSY`. The first AgentRun MUST continue unaffected.


## AC-024

Requirements:
- REQ-013

Criterion:
When pause is requested for a running AgentRun, the API MUST record or propagate a pause request without forcibly terminating an in-flight indivisible operation. At the next safe checkpoint, the run MUST persist recoverable state and transition to Core status `paused`.


## AC-025

Requirements:
- REQ-013

Criterion:
When resume is requested for a paused AgentRun, the run MUST transition back to execution using persisted checkpoint state. Resume requests for terminal runs MUST be rejected with HTTP 409 and MUST NOT mutate the run.


## AC-026

Requirements:
- REQ-013

Criterion:
While a pause request is pending but the run remains Core status `running`, the API may expose a derived display state indicating a pending pause, but it MUST NOT persist a new authoritative Core lifecycle state named `pausing`.


## AC-027

Requirements:
- REQ-014

Criterion:
When `GET /api/v1/runs/{run_id}/timeline?after_sequence=N` is called, the response MUST contain only timeline events with sequence values greater than `N`, in ascending order, and MUST return the highest emitted sequence as the next cursor.


## AC-028

Requirements:
- REQ-014

Criterion:
Timeline events MUST be derived from persisted trace and formal research artifacts such as planning, ResearchSession, query, retrieval, evidence, claim, synthesis, status, warning, or error events. The API MUST NOT expose hidden model chain-of-thought, private scratchpad content, hidden system prompts, or private provider reasoning.


## AC-029

Requirements:
- REQ-015

Criterion:
When a Turn has a completed final answer with supporting evidence, the API MUST return structured citations that identify the referenced paper and evidence identity and include available page/locator/provenance information. These citations MUST be separate from a paper's own bibliographic citation records.


## AC-030

Requirements:
- REQ-016

Criterion:
When Wiki overview or status is requested for a Workspace, the API MUST expose Base Wiki and Agentic Wiki state as structured data. A Workspace with no schema MUST report Base Wiki as unsupported rather than returning HTTP 500.


## AC-031

Requirements:
- REQ-016

Criterion:
When Base Wiki build is requested for an eligible schema-bound Workspace, the existing Workspace Wiki build service MUST be invoked. Page, entity, and Agentic Wiki endpoints MUST return structured data and MUST NOT return backend-rendered HTML as the canonical representation.


## AC-032

Requirements:
- REQ-016

Criterion:
When unified Wiki search is requested, results MAY contain Base Wiki and Agentic Wiki hits, but every result MUST preserve its `source_kind` so the two knowledge sources remain distinguishable.


## AC-033

Requirements:
- REQ-017

Criterion:
When a known domain or command error occurs, the API MUST return a structured envelope containing `error.code`, `error.message`, and `error.details`. Known state conflicts MUST return HTTP 409 rather than HTTP 500.


## AC-034

Requirements:
- REQ-017

Criterion:
When a PDF exceeds the configured upload limit the API MUST return HTTP 413; when the request body fails schema validation the API MUST return HTTP 422; when a requested resource does not exist the API MUST return HTTP 404.


## AC-035

Requirements:
- REQ-018

Criterion:
When two independent HTTP requests use Product Layer functionality, they MUST NOT share the same SQLAlchemy Session object. Closing one request's session MUST NOT invalidate the other request's Product operations.


## AC-036

Requirements:
- REQ-018
- REQ-012

Criterion:
When an AgentRun is scheduled into the local worker, the worker MUST create or acquire its own valid persistence/runtime scope and MUST NOT continue using a request-owned SQLAlchemy Session after the originating HTTP request has ended.


## AC-037

Requirements:
- REQ-019

Criterion:
When the API process starts and finds a persisted AgentRun with status `running` but no active local worker owns that run, the run MUST be reconciled to `paused` and remain eligible for explicit resume. The API MUST NOT automatically resume it.


## AC-038

Requirements:
- REQ-020

Criterion:
When internal Product/Core models gain fields that are not part of the API DTOs, the generated OpenAPI contract and serialized API response MUST remain unchanged unless the API DTO is explicitly revised.
