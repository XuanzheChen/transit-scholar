# Implementation Recommendation

## REQUIRED

- Keep FastAPI and the existing `/api/v1/` formal API surface as the selected transport layer.
- Keep the API Layer thin: HTTP routing, DTO validation, error translation, file transport, local scheduling, run-control exposure, and read-model projection only.
- Preserve Product Layer and Agent Core ownership of AgentRun, ResearchSession, retrieval, evidence, claims, memory, Wiki evolution, Workspace revision, and Schema binding.
- Introduce an application-scope runtime/composition context created once during FastAPI startup/lifespan.
- The application-scope context MUST expose reusable process-safe dependencies including Settings, database engine, Session factory, RuntimeFactory, configured shared provider/LLM client, SchemaCatalog, and equivalent safe dependencies.
- Replace request-time full `build_local_product()` calls with a lightweight request factory that creates only a fresh SQLAlchemy Session plus Product facade.
- Background AgentRun workers MUST reuse application-scope dependencies but own a fresh Product/session scope.
- Full Alembic/bootstrap/provider/runtime composition MUST occur at application startup rather than for every ordinary HTTP request.
- Prompt admission MUST be atomic and MUST occur before `prepare_message()` persists Turn/Run state.
- Busy rejection MUST produce `RUNNER_BUSY` with no Product mutation.
- Reservation MUST be released if preparation fails.
- Executor submission failure MUST release reservation and MUST NOT leave a Workspace-blocking orphan non-terminal run.
- Preserve non-blocking prompt submission: prepare identities, commit, schedule, return HTTP 202, then execute in the local worker.
- Keep v2 single-run and local; do not add distributed queue infrastructure.
- Harden the local execution manager for atomic reservation, exception-safe submission, active ownership cleanup, Future cleanup, fresh worker scope, and deterministic shutdown.
- Cooperative pause MUST propagate into the active session/runtime path rather than checking only between ResearchSessions.
- Pause checks MUST occur at existing safe durable boundaries after indivisible work completes.
- Pause MUST preserve resumable ResearchSession/AgentRun state and MUST NOT become cancel.
- Public resume MUST continue a paused run using the same AgentRun identity.
- Do not persist a new `pausing` Core state.
- Timeline MUST be an API-safe projection; internal Trace may retain richer diagnostics.
- Public Timeline MUST use normalized event kinds and sanitized fields.
- Raw `str(exc)`, raw provider output, hidden reasoning, hidden prompts, and scratchpad content MUST NOT be serialized into public Timeline events.
- Use typed domain/application errors for API semantics and remove broad generic exception mappings that can hide programming/runtime failures.
- Unexpected exceptions MUST map to HTTP 500 with stable `INTERNAL_ERROR` and no private exception detail.
- Preserve the existing Paper, Schema, Workspace, Conversation, Run, Citation, and Wiki API capabilities unless this v2 contract explicitly tightens behavior.
- Reuse existing Layer 1 paper workflows.
- Preserve Paper-in-Workspace delete protection.
- Preserve immutable Schema versions and immutable Workspace Schema binding.
- Preserve Workspace mutation blocking for non-terminal dependent AgentRuns.
- Preserve distinction between bibliography citations and Agent-answer evidence citations.
- Preserve structured Wiki JSON and Base Wiki versus Agentic Wiki source identity.
- Startup reconciliation MUST pause unowned persisted `running` runs and MUST NOT auto-resume.
- Shutdown MUST release execution-manager resources and SHOULD request cooperative pause/checkpoint before teardown when a run is active.
- `/api/v1/capabilities` MUST reflect actual Agent runtime/provider availability rather than static route existence.
- Preserve explicit API DTO and OpenAPI isolation from Product/Core model evolution.

## RECOMMENDED

- Prefer a lifespan-managed `ApiRuntimeContext`.
- Prefer a reservation-token or context-manager API for runner admission.
- Prefer deterministic compensation if preparation succeeds but scheduling fails.
- Prefer safe pause checks after provider call completion, role completion, action commit, retrieval batch completion, and existing checkpoint writes.
- Prefer Timeline failure DTOs containing stable `code`, safe `summary`, and optional `retryable`.
- Prefer centralized exception translation helpers.
- Prefer done callbacks or bounded retention for Future cleanup.
- Prefer graceful shutdown as: request pause -> allow bounded checkpoint opportunity -> release executor resources.
- Preserve the existing seven API domains: System, Papers, Schemas, Workspaces, Conversations, Runs, Wiki.
