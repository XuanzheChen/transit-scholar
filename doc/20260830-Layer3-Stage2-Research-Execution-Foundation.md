# Layer3 Stage2: Research Execution Foundation

Layer3 Stage2 provides durable, framework-neutral control-plane records for research
execution within an existing active Workspace. It does not implement planning,
retrieval, reasoning, memory, claims, or an Agentic Loop.

## Implemented contracts

- `AgentRunService` creates and reads one user-goal run bound to an active Workspace.
  Each run persists its Workspace identity, the observed Workspace revision, lifecycle
  status, and timestamps. Workspace validation is delegated to the Stage1
  `WorkspaceService`.
- `AgentRunService` creates, lists, reads, and updates `ResearchSession` records.
  A session belongs to exactly one run and contains one research question. Run/session
  creation has no ResearchPlan prerequisite or parameter.
- `ResearchStateService` saves and loads one JSON-compatible working-state payload per
  owned session. The payload is intentionally extensible and does not require any
  planner, claim, memory, or runtime-framework fields.
- `AgentTraceService` appends structured execution events in a per-run sequence. Events
  can be run-level or session-scoped; reads return deterministic full-run or
  session-filtered streams.
- `EvidenceLocator` and `EvidenceSpan` describe source provenance only. The initial
  Paper form supports Workspace, Paper, source kind, optional block, pages, and span.

## Public modules

- `transit_scholar.layer3.execution`
- `transit_scholar.layer3.state`
- `transit_scholar.layer3.trace`
- `transit_scholar.layer3.evidence`

All public modules are usable without LangGraph or another Agent runtime framework.
Persistence is supplied by the existing SQLAlchemy/Alembic database boundary and the
existing Stage1 Workspace service remains authoritative for Workspace lifecycle rules.
