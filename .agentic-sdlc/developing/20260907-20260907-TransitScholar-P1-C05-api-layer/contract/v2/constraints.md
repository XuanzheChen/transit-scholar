# Constraints

## C-001

The API Layer MUST NOT become an alternative source of truth for AgentRun, ResearchSession, Query, Evidence, Claim, Memory, Wiki, Workspace revision, or Schema binding state.


## C-002

The API Layer MUST NOT expose direct mutation endpoints for Claim, Evidence, ResearchSession, Role, Planner, Ledger, or Memory internals.


## C-003

Formal product API endpoints MUST remain versioned under `/api/v1/`.


## C-004

FastAPI remains the selected API framework. Replacing it is outside this Contract.


## C-005

The product remains a local single-user, single-process application. Redis, Celery, RabbitMQ, Kafka, distributed workers, and equivalent external job systems MUST NOT be introduced by this work.


## C-006

At most one AgentRun may be actively executed by the v2 local execution manager at one time.


## C-007

A rejected `RUNNER_BUSY` prompt submission MUST NOT persist a ConversationTurn or non-terminal AgentRun for the rejected request.


## C-008

A single SQLAlchemy Session MUST NOT be shared across independent HTTP requests or between a completed HTTP request and a background AgentRun worker.


## C-009

The full product bootstrap/migration/runtime composition path MUST NOT be executed once per ordinary HTTP request.


## C-010

Pause MUST be cooperative, resumable, and checkpoint-safe. It MUST NOT be implemented as cancellation or forced termination of an indivisible in-flight provider operation.


## C-011

A persisted authoritative `pausing` AgentRun lifecycle status MUST NOT be introduced.


## C-012

Hidden model chain-of-thought, private scratchpads, hidden prompts, raw provider output, and raw internal exception diagnostics MUST NOT be exposed through public API Timeline responses.


## C-013

Generic exception classes such as `ValueError` MUST NOT serve as the primary API semantic distinction between not-found, conflict, and internal server failure.


## C-014

Unknown server exceptions MUST remain HTTP 500 failures and MUST NOT be disguised as 404 or 409 responses.


## C-015

Workspace Schema binding MUST remain immutable after Workspace creation.


## C-016

Existing Schema versions MUST remain immutable; modifications require new versions.


## C-017

Removing a Paper from a Workspace MUST NOT delete the Paper from the global library.


## C-018

Library-level Paper deletion MUST remain blocked while the Paper belongs to an active Workspace.


## C-019

Workspace membership, archive, and delete mutations MUST remain blocked while a non-terminal AgentRun depends on the Workspace revision.


## C-020

PDF content endpoints MUST NOT accept arbitrary caller-supplied filesystem paths.


## C-021

Paper bibliography citations and Agent-answer evidence citations MUST remain distinct API concepts.


## C-022

Wiki API output MUST remain structured data rather than requiring backend-rendered HTML.


## C-023

Base Wiki and Agentic Wiki MUST remain distinguishable, including `source_kind` in unified search.


## C-024

A no-schema Workspace MUST report Base Wiki as unsupported rather than as an internal server error.


## C-025

Existing Layer 1 ingestion and paper-maintenance workflows MUST be reused rather than duplicated in API routers.


## C-026

Existing Product Layer and Agent Core orchestration remain the execution authority. API routers MUST NOT reproduce the Agent loop.


## C-027

The API MUST use explicit DTOs so internal Product/Core field evolution does not automatically modify the HTTP contract.


## C-028

The API MUST NOT expose API credentials, provider secrets, raw local absolute paths, authentication tokens, or private runtime diagnostics in normal product responses.


## C-029

Application shutdown MUST not intentionally wait indefinitely for a long AgentRun without first requesting cooperative pause/checkpoint behavior when an active run exists.


## C-030

Capability reporting MUST distinguish route presence from actual executable Agent runtime availability.
