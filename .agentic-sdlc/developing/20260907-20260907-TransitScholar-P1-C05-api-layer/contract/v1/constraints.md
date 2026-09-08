# Constraints

## C-001

The API Layer MUST NOT become an alternative source of truth for AgentRun, ResearchSession, Query, Evidence, Claim, Memory, Wiki, Workspace revision, or Schema binding state.


## C-002

The API Layer MUST NOT expose direct mutation endpoints for Claim, Evidence, ResearchSession, Role, Planner, Ledger, or Memory internals.


## C-003

Formal API endpoints MUST be versioned under `/api/v1/`.


## C-004

FastAPI is the selected API framework. No replacement API framework is part of this Contract.


## C-005

The first version is a local single-user product and MUST NOT introduce Redis, Celery, RabbitMQ, Kafka, distributed workers, multi-process worker orchestration, or equivalent external job infrastructure.


## C-006

The first version MUST execute at most one AgentRun concurrently through the local execution manager.


## C-007

A single SQLAlchemy Session MUST NOT be shared across independent HTTP requests or between an originating HTTP request and a background AgentRun worker after the request ends.


## C-008

Pause MUST be cooperative and checkpoint-safe. The implementation MUST NOT rely on forcibly terminating an in-flight indivisible LLM/provider/database operation.


## C-009

The existing Core lifecycle vocabulary MUST remain authoritative. A persisted `pausing` lifecycle state MUST NOT be introduced by the API Layer.


## C-010

Hidden model chain-of-thought, private scratchpads, hidden system prompts, and private provider reasoning MUST NOT be exposed through Timeline or any other API response.


## C-011

Workspace Schema binding MUST remain immutable after Workspace creation.


## C-012

An existing Schema version MUST remain immutable. Changes require creation of a new version.


## C-013

Removing a Paper from a Workspace MUST NOT delete the Paper from the global library.


## C-014

Library-level Paper deletion MUST be blocked while the Paper remains a member of any active Workspace.


## C-015

Workspace membership, archive, and delete operations MUST be blocked while a non-terminal AgentRun depends on that Workspace revision.


## C-016

PDF content endpoints MUST NOT accept arbitrary filesystem paths supplied by callers.


## C-017

Wiki API responses MUST use structured data as the canonical representation. Backend-rendered HTML MUST NOT become the required Wiki API contract.


## C-018

Base Wiki and Agentic Wiki MUST remain distinguishable, including through unified search result `source_kind`.


## C-019

A no-schema Workspace MUST report Base Wiki as unsupported, not as an internal server error.


## C-020

Paper bibliography citations and Agent answer evidence citations MUST remain distinct API concepts.


## C-021

Existing Layer 1 ingestion, metadata, duplicate, citation, enrichment, soft-delete, and restore workflows MUST be reused rather than reimplemented in API routers.


## C-022

Existing Product Layer and Agent Core orchestration behavior MUST remain the execution authority. API routers MUST NOT reproduce Agent loop logic.


## C-023

The formal API MUST use explicit API DTOs so internal Product/Core model evolution does not automatically change the HTTP contract.


## C-024

Known state conflicts MUST use stable HTTP 409 responses rather than being surfaced as HTTP 500.


## C-025

The API MUST NOT expose local absolute storage paths, API credentials, provider secrets, or authentication tokens in normal product responses.


## C-026

The existing acceptance-panel Web/API implementation may remain during migration and MUST NOT be destructively replaced until formal `/api/v1/` coverage and regression verification exist.
