# Implementation Recommendation

## REQUIRED

- Implement one product-layer `RuntimeFactory`.
- Implement one non-owning `RunScope` container for one AgentRun's composed runtime objects.
- Load the authoritative AgentRun before constructing Workspace-bound retrieval/knowledge objects.
- Bind Workspace-scoped knowledge/retrieval using the AgentRun's existing Workspace identity and revision.
- Compose existing authoritative services: Workspace, execution, research state, ledger, trace, retrieval/knowledge, Role bridge, ActionValidator/Executor, RoleRuntime, MainResearchRuntime, run coordinator, RunResearchRuntime, Episodic Memory, and L3S7 lifecycle.
- Reuse the existing production run coordinator builder/semantic coordinator path.
- Reuse existing FileRoleExecutionStore for Role recovery.
- Add only the durable local store needed for the existing RunResearchRuntime state-store seam.
- Ensure production run-state checkpoint publication occurs after the SQLAlchemy commit that makes preceding authoritative mutations durable.
- Support rebuilding a new RunScope for an existing AgentRun and existing durable state.

## RECOMMENDED

- Place RuntimeFactory, RunScope, and the run-state store under `src/transit_scholar/product/runtime.py` unless repository organization strongly favors a small adjacent module.
- Use an AgentRun-scoped local path such as `data/layer3/runs/<agent_run_id>/run_state.json` for run-orchestration state.
- Use temporary-file plus atomic replace semantics consistent with the existing FileRoleExecutionStore pattern.
- Keep Application-scope inputs simple: settings, DB session factory, shared structured LLM client, Role definitions/registry configuration, and runtime configuration.
- Let each RunScope own/use one SQLAlchemy session suitable for the run's authoritative services rather than introducing a DI framework.
- Implement checkpoint commit ordering with a thin wrapper/callback around the run-state store seam rather than a new Unit-of-Work abstraction.
