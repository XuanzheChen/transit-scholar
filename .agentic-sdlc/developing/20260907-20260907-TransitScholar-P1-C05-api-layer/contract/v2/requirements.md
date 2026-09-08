# Requirements

## REQ-001

Title: Versioned FastAPI Product API

Description:
TransitScholar MUST provide a formal local HTTP API under `/api/v1/` using FastAPI. The API Layer MUST remain a transport and presentation boundary over the Product Layer and Agent Core. It MUST NOT reimplement Agent orchestration, retrieval planning, evidence admission, claim reasoning, memory evolution, or other internal Agent logic.

Priority: Must


## REQ-002

Title: Application-Scope Composition and Request-Scoped Product Facades

Description:
The API process MUST construct expensive and process-safe application dependencies once during application startup, including the database engine/session factory, RuntimeFactory, shared LLM/provider client when configured, SchemaCatalog, settings, and equivalent process-scope dependencies. Each HTTP request and each AgentRun worker MUST create its own fresh SQLAlchemy Session and Product facade. A request MUST NOT execute the full `build_local_product()` bootstrap path on every call.

Priority: Must


## REQ-003

Title: Atomic Prompt Admission

Description:
Prompt submission MUST acquire the single-run execution admission slot atomically before creating persistent Conversation Turn or AgentRun state. If execution capacity is unavailable, the request MUST fail before Product mutation. A rejected `RUNNER_BUSY` submission MUST NOT leave an orphan ConversationTurn, created AgentRun, or any Workspace-blocking non-terminal run.

Priority: Must


## REQ-004

Title: Single-Run Local Execution Manager Lifecycle

Description:
The API MUST execute at most one AgentRun concurrently in the local process. The execution manager MUST atomically reserve and release its execution slot, release the slot if executor submission itself fails, clean up completed Future references, and ensure a worker owns a fresh Product/session scope. No distributed queue, external broker, or multi-worker orchestration system is required.

Priority: Must


## REQ-005

Title: Non-Blocking Conversation Prompt Submission

Description:
Submitting a user prompt to a Conversation MUST persist a Conversation Turn and associated AgentRun only after execution admission succeeds, return `turn_id` and `agent_run_id` without waiting for the run to finish, and schedule that persisted AgentRun for local execution. Completed Agent output MUST later be persisted back to the same Conversation Turn.

Priority: Must


## REQ-006

Title: Cooperative Pause at Safe Runtime Checkpoints

Description:
A running AgentRun MUST support cooperative pause and explicit resume. Pause MUST propagate below run-level orchestration into the active session/runtime execution path so the system can stop at the nearest safe checkpoint after an indivisible external operation completes. Safe checkpoints SHOULD include post-provider-call, post-role, post-action-commit, post-retrieval-batch, or equivalent durable boundaries. Pause MUST preserve resumable state and MUST NOT be implemented as cancellation.

Priority: Must


## REQ-007

Title: Run State and Resume Semantics

Description:
The API MUST expose AgentRun state and control operations. Core lifecycle status MUST remain authoritative and use existing values such as `created`, `running`, `paused`, `completed`, `failed`, and `cancelled`. A transient API display state such as `pausing` MAY be derived from `running` plus a pause request, but MUST NOT become a new persisted Core lifecycle state. Resume through the public API MUST operate on paused runs and MUST continue the same AgentRun from persisted checkpoint state.

Priority: Must


## REQ-008

Title: Sanitized Incremental Run Timeline

Description:
The API MUST expose an incremental Timeline for AgentRun progress using a monotonically increasing sequence cursor. Timeline events MUST be derived from persisted trace and formal research artifacts and normalized into API-facing event kinds. Public Timeline output MUST NOT expose hidden model chain-of-thought, hidden prompts, private scratchpads, raw provider output, raw internal exception strings, or provider-private diagnostic text.

Priority: Must


## REQ-009

Title: Typed Domain-to-HTTP Error Mapping

Description:
Known domain and application failures MUST be represented by typed exceptions or equally explicit stable error categories before translation to HTTP. API routers MUST NOT use broad `ValueError`, `RuntimeError`, or similar generic exception catches as the primary semantic mapping for resource-not-found or state-conflict responses. Unknown exceptions MUST remain internal failures and be returned as HTTP 500 using the stable API error envelope.

Priority: Must


## REQ-010

Title: Stable Error Envelope

Description:
All known API errors MUST use a stable envelope containing `error.code`, `error.message`, and `error.details`. Resource absence MUST map to HTTP 404, state conflicts to HTTP 409, request validation to HTTP 422, oversized uploads to HTTP 413, provider unavailability to HTTP 503 where applicable, and unexpected internal failures to HTTP 500.

Priority: Must


## REQ-011

Title: Paper Import and Library Management API

Description:
The formal API MUST expose existing Layer 1 capabilities needed by the local product: PDF import, paper listing and detail, readiness/processing state, metadata candidate retrieval, manual metadata correction, reconcile, enrichment retrieval/refresh, duplicate relation review/adjudication, bibliographic citation retrieval, library soft delete, and restore. Library deletion MUST be rejected while the paper remains a member of any active Workspace.

Priority: Must


## REQ-012

Title: Safe Registered PDF Retrieval

Description:
The API MUST allow retrieval of registered paper PDF files by persisted file identity. The endpoint MUST resolve the storage location from persisted metadata and MUST NOT accept arbitrary caller-supplied filesystem paths.

Priority: Must


## REQ-013

Title: Unified Immutable Schema Catalog

Description:
The API MUST expose one Schema Catalog containing built-in and user-created schema versions. It MUST support listing schemas, retrieving a schema definition, validating a draft without persistence, and creating immutable user schema versions. Workspace creation, schema materialization, and schema-dependent Wiki behavior MUST resolve schema identity through the same catalog.

Priority: Must


## REQ-014

Title: Workspace Lifecycle, Membership, and Immutable Schema Binding

Description:
The API MUST expose Workspace creation, listing, detail, archive, delete, paper membership listing/add/remove, schema binding state, and schema materialization state. Workspace creation MUST support either no schema or one explicit schema version. Once created, Workspace schema mode and binding identity MUST be immutable. Workspace membership, archive, and delete operations MUST be rejected while a non-terminal AgentRun depends on that Workspace revision.

Priority: Must


## REQ-015

Title: Conversation Lifecycle and Persistent Turn History

Description:
The API MUST expose creation and listing of Conversations scoped to a Workspace, complete Conversation history retrieval, individual Turn retrieval, and non-blocking prompt submission. Conversation history MUST preserve user message, resolved user goal, associated AgentRun identity, execution status, and completed assistant response.

Priority: Must


## REQ-016

Title: Structured Final Answer Citations

Description:
Completed Conversation Turns MUST expose the final assistant answer together with structured evidence citations sufficient to identify supporting paper, available page or source locator, evidence identity, and research-session provenance where available. Paper bibliography citations and Agent-answer evidence citations MUST remain distinct API concepts.

Priority: Must


## REQ-017

Title: Structured Base Wiki and Agentic Wiki API

Description:
The API MUST expose Workspace Wiki overview/status, Base Wiki build, Base Wiki pages/entities, Agentic Wiki entries, and unified Wiki search as structured data. Backend-rendered HTML MUST NOT be the canonical API representation. Base Wiki and Agentic Wiki results MUST remain distinguishable through `source_kind`. No-schema Workspaces MUST report Base Wiki as unsupported rather than as an internal error.

Priority: Must


## REQ-018

Title: Startup Reconciliation of Interrupted Runs

Description:
On API startup, persisted AgentRuns marked `running` that are not owned by a worker in the current process MUST be reconciled to a resumable `paused` state. The API MUST NOT automatically resume them.

Priority: Must


## REQ-019

Title: Graceful Shutdown and Execution Cleanup

Description:
Application shutdown MUST not leave execution-manager ownership state inconsistent. If an AgentRun is active, shutdown SHOULD request cooperative pause and allow durable checkpointing before worker teardown when possible. Shutdown MUST release process-local execution resources and MUST NOT rely indefinitely on `wait=True` while a long-running Agent continues without a pause request.

Priority: Must


## REQ-020

Title: Capability Reporting Reflects Runtime Availability

Description:
`GET /api/v1/capabilities` MUST report configured product capabilities without falsely claiming executable Agent capability when required runtime/provider dependencies are unavailable. Schema/library-only operation may remain available in an installation where the LLM/provider runtime is unavailable.

Priority: Must


## REQ-021

Title: Explicit API DTO and OpenAPI Boundary

Description:
API request and response models MUST be explicit API-layer DTOs rather than direct Product/Core models. FastAPI MUST expose a stable OpenAPI contract for `/api/v1/`. Internal Product/Core field additions MUST NOT automatically alter external HTTP responses.

Priority: Must
