# Implementation Recommendation

## REQUIRED

- Use FastAPI as the formal API framework and serve the formal product contract under `/api/v1/`.

- Keep the API Layer thin:
  - HTTP routing.
  - Request/response DTO validation.
  - Error translation.
  - File upload/download transport.
  - Local AgentRun scheduling.
  - Run-control signal exposure.
  - Conversion of Product/Core read models into API read models.

- Existing Product Layer and Agent Core services MUST remain the authoritative owners of:
  - AgentRun lifecycle.
  - ResearchSession lifecycle.
  - Retrieval.
  - Query creation.
  - Evidence.
  - Claims.
  - Memory.
  - Agentic Wiki knowledge evolution.
  - Workspace revision semantics.
  - Schema binding semantics.

- Do not expose direct mutation endpoints for:
  - Claims.
  - Evidence.
  - ResearchSessions.
  - Roles.
  - Planner state.
  - Ledger state.
  - Memory state.

- Define explicit API DTOs for requests and responses. Do not use Product/Core model classes as the stable external HTTP contract.

- Add a formal API application package separate from the existing acceptance-panel web implementation. Existing acceptance-panel routes may remain temporarily for regression compatibility, but new product endpoints MUST use `/api/v1/`.

- Use request-scoped SQLAlchemy Sessions or request-scoped Product facades. Do not place a single long-lived `TransitScholarProduct` holding one SQLAlchemy Session in global FastAPI application state.

- Reuse thread-safe process-level dependencies where appropriate, including settings, runtime factory configuration, and provider clients that are already designed for process-level reuse.

- Introduce a non-blocking message preparation path in the Product Layer:
  - Persist Conversation Turn.
  - Resolve conversation context into a standalone user goal.
  - Create AgentRun.
  - Link Turn and AgentRun.
  - Commit these identities before execution starts.
  - Return identities without running the Agent synchronously.

- Preserve the existing synchronous convenience behavior by implementing existing `submit_message()` semantics as the equivalent of:
  - prepare message/run.
  - execute prepared run.
  - persist final response.

- Introduce a local AgentRun execution manager with one active worker slot in v1.
  - No Redis.
  - No Celery.
  - No RabbitMQ.
  - No distributed worker system.
  - The manager MUST accept a persisted `agent_run_id`, not a request-owned SQLAlchemy Session.

- Add formal cooperative run control.
  - Running AgentRuns MUST accept a pause request.
  - Pause MUST be observed by the runtime at safe checkpoints.
  - A successful pause MUST persist recoverable state and transition the run to `paused`.
  - Resume MUST continue the same AgentRun from persisted runtime state.
  - Do not create a new authoritative `pausing` Core state.
  - A derived API field such as `pause_requested` or `display_status` may represent a pending pause.

- Reconcile interrupted runs on API startup:
  - Persisted `running` run.
  - No corresponding worker in the current process.
  - Transition to `paused`.
  - Do not auto-resume.

- Build the Run Timeline from persisted trace and formal research artifacts.
  - Every returned timeline event MUST have a stable monotonic sequence.
  - Support `after_sequence`.
  - Normalize internal event types into API-facing kinds such as:
    - `planning`
    - `research_session`
    - `query`
    - `retrieval`
    - `evidence`
    - `claim`
    - `synthesis`
    - `status`
    - `warning`
    - `error`
  - Do not expose hidden chain-of-thought, internal scratchpads, hidden system prompts, or raw provider reasoning.

- Keep paper bibliography citations distinct from Agent answer evidence citations.

- Agent answer citation DTOs MUST preserve sufficient provenance to identify, where available:
  - Paper identity.
  - Paper title.
  - Page or source locator.
  - Block/character locator if available.
  - Quote if formally available from admitted evidence.
  - Evidence identity.
  - ResearchSession identity.

- Reuse the existing Layer 1 ingestion, metadata, duplicate, enrichment, citation, soft-delete, and restore workflows rather than reimplementing them in API routers.

- Before library-level paper soft deletion, enforce a product-level guard that rejects deletion while the paper belongs to any active Workspace.

- Distinguish:
  - Removing a paper from a Workspace.
  - Soft-deleting a paper from the global library.

- PDF content serving MUST resolve the file from a registered file identity. Do not expose an endpoint accepting arbitrary caller-supplied filesystem paths.

- Implement a unified Schema Catalog used consistently by:
  - Schema API listing.
  - Schema validation.
  - User schema creation.
  - Workspace creation.
  - Workspace schema resolution.
  - Workspace schema materialization.
  - Wiki build operations that depend on Schema identity.

- Built-in schemas and user schemas MUST be discoverable through the same catalog abstraction.

- User schema versions MUST be immutable once created.
  - No in-place update endpoint for an existing schema ID/version.
  - A modified schema requires a new version.

- Workspace schema binding MUST remain immutable after Workspace creation.

- Prevent Workspace boundary mutation while a non-terminal AgentRun is associated with the Workspace revision.
  - Block paper membership changes.
  - Block archive.
  - Block delete.
  - Return HTTP 409.

- Expose Wiki as structured JSON.
  - Do not make backend-rendered HTML the canonical Wiki API representation.
  - Preserve Base Wiki and Agentic Wiki distinctions.
  - Preserve `source_kind` in unified search.
  - Report Base Wiki `unsupported` for no-schema Workspaces.

- Use one stable error envelope:
  - `error.code`
  - `error.message`
  - `error.details`

- Map known state conflicts to HTTP 409.

- Required primary API groups:
  - System.
  - Papers.
  - Schemas.
  - Workspaces.
  - Conversations.
  - Runs.
  - Wiki.

- Required primary endpoint surface:

  System:
  - `GET /api/v1/health`
  - `GET /api/v1/capabilities`

  Papers:
  - `GET /api/v1/papers`
  - `POST /api/v1/papers/import`
  - `GET /api/v1/papers/{paper_id}`
  - `PATCH /api/v1/papers/{paper_id}/metadata`
  - `DELETE /api/v1/papers/{paper_id}`
  - `POST /api/v1/papers/{paper_id}/restore`
  - `POST /api/v1/papers/{paper_id}/reconcile`
  - `GET /api/v1/papers/{paper_id}/files`
  - `GET /api/v1/files/{file_id}/content`
  - `GET /api/v1/papers/{paper_id}/citations`
  - `GET /api/v1/papers/{paper_id}/metadata-candidates`
  - `GET /api/v1/papers/{paper_id}/enrichment`
  - `POST /api/v1/papers/{paper_id}/enrichment/refresh`
  - `GET /api/v1/papers/{paper_id}/duplicate-relations`
  - `POST /api/v1/duplicate-relations/{relation_id}/resolve`

  Schemas:
  - `GET /api/v1/schemas`
  - `POST /api/v1/schemas/validate`
  - `POST /api/v1/schemas`
  - `GET /api/v1/schemas/{schema_id}/versions/{version}`

  Workspaces:
  - `GET /api/v1/workspaces`
  - `POST /api/v1/workspaces`
  - `GET /api/v1/workspaces/{workspace_id}`
  - `POST /api/v1/workspaces/{workspace_id}/archive`
  - `DELETE /api/v1/workspaces/{workspace_id}`
  - `GET /api/v1/workspaces/{workspace_id}/papers`
  - `POST /api/v1/workspaces/{workspace_id}/papers`
  - `DELETE /api/v1/workspaces/{workspace_id}/papers/{paper_id}`
  - `GET /api/v1/workspaces/{workspace_id}/schema`
  - `GET /api/v1/workspaces/{workspace_id}/papers/{paper_id}/schema`
  - `POST /api/v1/workspaces/{workspace_id}/papers/{paper_id}/schema/materialize`

  Conversations:
  - `GET /api/v1/workspaces/{workspace_id}/conversations`
  - `POST /api/v1/workspaces/{workspace_id}/conversations`
  - `GET /api/v1/conversations/{conversation_id}`
  - `POST /api/v1/conversations/{conversation_id}/turns`
  - `GET /api/v1/turns/{turn_id}`

  Runs:
  - `GET /api/v1/runs/{agent_run_id}`
  - `GET /api/v1/runs/{agent_run_id}/timeline`
  - `POST /api/v1/runs/{agent_run_id}/pause`
  - `POST /api/v1/runs/{agent_run_id}/resume`

  Wiki:
  - `GET /api/v1/workspaces/{workspace_id}/wiki`
  - `GET /api/v1/workspaces/{workspace_id}/wiki/status`
  - `POST /api/v1/workspaces/{workspace_id}/wiki/build`
  - `GET /api/v1/workspaces/{workspace_id}/wiki/pages`
  - `GET /api/v1/workspaces/{workspace_id}/wiki/pages/{page_id}`
  - `GET /api/v1/workspaces/{workspace_id}/wiki/entities`
  - `GET /api/v1/workspaces/{workspace_id}/wiki/entities/{entity_id}`
  - `GET /api/v1/workspaces/{workspace_id}/wiki/agentic-entries`
  - `GET /api/v1/workspaces/{workspace_id}/wiki/agentic-entries/{entry_id}`
  - `GET /api/v1/workspaces/{workspace_id}/wiki/search`

## RECOMMENDED

- Organize formal API code approximately as:

  - `src/transit_scholar/api/app.py`
  - `src/transit_scholar/api/dependencies.py`
  - `src/transit_scholar/api/errors.py`
  - `src/transit_scholar/api/routers/**`
  - `src/transit_scholar/api/schemas/**`
  - `src/transit_scholar/api/adapters/**`
  - `src/transit_scholar/api/runtime/**`

- Prefer a `ThreadPoolExecutor(max_workers=1)` or equivalent local single-worker implementation for v1 AgentRun execution.

- Prefer a lightweight process-local run-control registry or equivalent signal abstraction for active run pause requests, while keeping persisted AgentRun status and runtime checkpoint as the durable source of truth.

- Prefer cursor-based timeline polling using `after_sequence` over SSE or WebSocket in v1.

- Prefer response DTOs that expose caller-relevant data only and omit:
  - Provider secrets.
  - Raw internal exceptions.
  - Hidden prompts.
  - Unnecessary internal persistence paths.
  - Raw internal trace payloads.

- Prefer a user schema storage layout under the application's existing local data root, organized by schema ID and version, while keeping built-in schema definitions in their existing source locations.

- Prefer retaining the existing acceptance-panel implementation during API migration until the formal `/api/v1/` contract and regression tests are complete.

- Prefer HTTP Range support for PDF content retrieval if practical within the selected FastAPI response implementation.

- Prefer preserving existing public Layer 1/2/3 interfaces and adding adapters or Product seams rather than refactoring stable Core modules solely for API convenience.
