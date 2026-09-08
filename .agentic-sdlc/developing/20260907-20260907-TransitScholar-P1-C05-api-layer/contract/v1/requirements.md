# Requirements

## REQ-001

Title: Versioned Local HTTP API

Description:
The system MUST provide a versioned local HTTP API under `/api/v1/` using FastAPI. The API Layer MUST act as a transport and presentation boundary over existing Product Layer and Agent Core capabilities. It MUST NOT reimplement Agent orchestration, retrieval planning, evidence admission, claim reasoning, memory evolution, or other internal Agent logic.

Priority: Must


## REQ-002

Title: System Health and Capability Discovery

Description:
The API MUST expose a health endpoint and a capability-discovery endpoint. Health information MUST indicate whether the local API is available. Capability information MUST allow callers to determine whether supported product capabilities such as run pause/resume, user schema creation, Base Wiki, Agentic Wiki, semantic wiki search, and PDF upload limits are available.

Priority: Must


## REQ-003

Title: Paper Import and Library Access

Description:
The API MUST expose the existing Layer 1 paper ingestion and library capabilities, including PDF import, paper listing, paper detail retrieval, second-layer readiness information, metadata candidate retrieval, enrichment state retrieval, and paper processing status.

Priority: Must


## REQ-004

Title: Paper Metadata, Duplicate, Citation, Deletion, and Restore Operations

Description:
The API MUST expose existing paper maintenance capabilities including manual metadata correction, processing reconciliation, enrichment refresh, duplicate relation inspection and adjudication, bibliographic citation retrieval, library-level soft deletion, and restoration. Library-level deletion MUST be rejected while the paper remains a member of any active Workspace.

Priority: Must


## REQ-005

Title: Safe PDF File Retrieval

Description:
The API MUST allow callers to discover files associated with a paper and retrieve registered PDF content for local document viewing. File retrieval MUST resolve files through stored paper/file identities and MUST NOT accept arbitrary filesystem paths from callers.

Priority: Must


## REQ-006

Title: Unified Schema Catalog

Description:
The API MUST expose a unified Schema Catalog containing both built-in schemas and user-created schemas. It MUST support listing schema versions, retrieving schema definitions, validating a schema draft without persistence, and creating immutable user schema versions. An existing schema version MUST NOT be modified in place.

Priority: Must


## REQ-007

Title: Workspace Lifecycle and Immutable Schema Binding

Description:
The API MUST expose Workspace creation, listing, detail retrieval, archive, and deletion. Workspace creation MUST support either no schema or one explicitly selected schema version. Once a Workspace is created, its schema mode and schema binding identity, including schema ID, version, and hash, MUST remain immutable.

Priority: Must


## REQ-008

Title: Workspace Paper Membership

Description:
The API MUST expose listing, adding, and removing paper membership for a Workspace. Removing a paper from a Workspace MUST NOT delete the paper from the global paper library. Workspace membership or lifecycle mutations MUST be rejected while the Workspace has a non-terminal AgentRun that depends on its current revision.

Priority: Must


## REQ-009

Title: Workspace Schema State and Materialization

Description:
For schema-bound Workspaces, the API MUST expose Workspace schema binding/readiness and per-paper schema materialization state. It MUST allow materialization through the existing schema processing services while preserving Workspace membership and immutable schema binding checks.

Priority: Must


## REQ-010

Title: Conversation Lifecycle and Persistent Turn History

Description:
The API MUST expose creation and retrieval of Conversation resources scoped to a Workspace, listing Conversations for a Workspace, reading a complete Conversation history, and reading an individual Conversation Turn. Conversation history MUST preserve user messages, resolved user goals, associated AgentRun identities, and completed assistant responses.

Priority: Must


## REQ-011

Title: Non-Blocking Prompt Submission

Description:
Submitting a user prompt to a Conversation MUST create and persist a Conversation Turn and associated AgentRun, return the new Turn ID and AgentRun ID without waiting for the AgentRun to finish, and schedule the AgentRun for local execution.

Priority: Must


## REQ-012

Title: Single-Run Local Execution Manager

Description:
The API Layer MUST provide a local execution manager for AgentRun execution. The first version MUST allow at most one active AgentRun execution at a time. A second execution request while another run is active MUST be rejected with a stable conflict response. No distributed queue, external broker, or multi-worker execution system is required.

Priority: Must


## REQ-013

Title: Run State, Cooperative Pause, and Resume

Description:
The API MUST expose AgentRun state and control operations. It MUST support a cooperative pause request for a running AgentRun and resume of a paused AgentRun. Pause MUST occur at a safe runtime checkpoint rather than forcibly terminating an in-flight indivisible external operation. The persisted Core lifecycle status MUST use existing lifecycle values and MUST NOT require a new authoritative `pausing` state.

Priority: Must


## REQ-014

Title: Incremental AgentRun Timeline

Description:
The API MUST expose an incremental Timeline for each AgentRun, derived from persisted runtime trace and formal research artifacts. Timeline retrieval MUST support a monotonically increasing sequence cursor so callers can request only events after a known sequence. Timeline output MUST expose formal execution progress and research artifacts, not hidden model chain-of-thought or private provider reasoning.

Priority: Must


## REQ-015

Title: Final Answer and Evidence Citation Exposure

Description:
Completed Conversation Turns MUST expose the final assistant answer together with structured citation information sufficient to identify supporting paper, page or locator data where available, evidence identity, and research-session provenance. Bibliographic citations belonging to a paper and evidence citations supporting an Agent answer MUST remain distinct API concepts.

Priority: Must


## REQ-016

Title: Structured Wiki API

Description:
The API MUST expose Workspace Wiki capabilities as structured data rather than backend-rendered HTML. It MUST expose Wiki overview and status, Base Wiki build, Base Wiki pages and entities, Agentic Wiki entries, and unified Wiki search across Base Wiki and Agentic Wiki. The API MUST preserve source-kind distinctions. A no-schema Workspace MUST report Base Wiki as unsupported rather than as an internal error.

Priority: Must


## REQ-017

Title: Stable API Error Contract

Description:
All domain and command failures exposed by the API MUST use a stable structured error envelope containing at least an error code, human-readable message, and details object. State conflicts MUST be represented as HTTP 409 rather than generic internal errors.

Priority: Must


## REQ-018

Title: Request-Scoped Database Sessions

Description:
HTTP requests MUST NOT share one long-lived SQLAlchemy Session through a singleton TransitScholarProduct instance. Each request that requires Product Layer access MUST use an independent database session or equivalent request-scoped Product facade while reusing only thread-safe process-level dependencies.

Priority: Must


## REQ-019

Title: Interrupted Run Reconciliation

Description:
When the local API process starts, it MUST detect persisted AgentRuns that are marked as running but have no execution worker in the current process. Such runs MUST be reconciled to a resumable paused state rather than being silently treated as actively executing or automatically resumed.

Priority: Must


## REQ-020

Title: Stable API DTO Boundary and OpenAPI Contract

Description:
API request and response models MUST be defined as explicit API DTOs rather than exposing Product Layer or Core models directly as the HTTP contract. FastAPI MUST expose an OpenAPI document for `/api/v1/`, and changes to internal Product/Core fields MUST NOT implicitly alter the external API contract.

Priority: Must
