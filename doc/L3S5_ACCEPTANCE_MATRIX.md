# L3S5 Acceptance Evidence Matrix

All references are executable pytest node IDs. The integration node starts with an empty
reasoning ledger and proves the runtime-created two-query chain through final provenance.

| AC | Executable evidence |
|---|---|
| AC-001 | `tests/integration/test_l3s5_end_to_end_runtime.py::test_database_backed_role_chain_persists_ledgers_trace_and_provenance`; `tests/test_l3s5_main_runtime_orchestration.py::test_main_cycle_dispatches_structured_actions_and_traces_committed_results` |
| AC-002 | `tests/test_l3s5_role_contracts.py::test_contracts_and_runtime_configuration_serialize_without_secrets`; `tests/test_l3s5_builtin_roles_contracts.py::test_all_five_roles_have_independent_contract_boundaries` |
| AC-003 | `tests/test_l3s5_role_contracts.py::test_registry_rejects_unregistered_roles`; `tests/test_l3s5_main_runtime_orchestration.py::test_unregistered_role_selection_fails_structured_validation_and_never_executes` |
| AC-004 | `tests/test_l3s5_role_contracts.py::test_same_runtime_contract_supports_one_and_multiple_steps` |
| AC-005 | `tests/test_l3s5_role_execution_persistence.py::test_role_execution_can_be_reloaded_with_working_state_and_usage`; `tests/test_l3s5_trace_role_runtime.py::test_role_events_use_agent_trace_shape_and_role_identity` |
| AC-006 | `tests/test_l3s5_main_runtime_orchestration.py::test_role_budget_is_not_clamped_to_main_remaining_budget`; `tests/test_l3s5_role_contracts.py::test_main_and_role_budgets_are_independent_and_externally_overridable` |
| AC-007 | `tests/test_l3s5_role_contracts.py::test_contracts_and_runtime_configuration_serialize_without_secrets`; `tests/test_l3s5_builtin_roles_contracts.py::test_every_runtime_profile_is_externally_overridable` |
| AC-008 | `tests/test_l3s5_role_contracts.py::test_contracts_and_runtime_configuration_serialize_without_secrets` plus the full L3S5 suite import/run with no LangGraph dependency |
| AC-009 | `tests/test_l3s5_role_contracts.py::test_same_runtime_contract_supports_one_and_multiple_steps` |
| AC-010 | `tests/test_l3s5_retry_role_runtime.py::test_structured_output_repair_does_not_increment_agentic_step`; `tests/test_l3s5_final_synthesis_artifact.py::test_unstructured_or_malformed_final_output_fails_schema_validation` |
| AC-011 | `tests/test_l3s5_final_synthesis_artifact.py::test_final_synthesis_returns_artifact_with_durable_source_provenance`; integration node above |
| AC-012 | `tests/test_l3s5_context_projection.py::test_same_snapshot_projects_observably_different_role_contexts` |
| AC-013 | `tests/test_l3s5_context_projection.py::test_omitted_context_has_no_role_input_access_path_or_memory_dependency` |
| AC-014 | `tests/test_l3s5_role_execution_persistence.py::test_interrupted_execution_reloads_through_fresh_store_and_runtime` |
| AC-015 | `tests/test_l3s5_recovery_role_runtime.py::test_resume_after_committed_action_does_not_replay_mutation` |
| AC-016 | `tests/test_l3s5_recovery_role_runtime.py::test_resume_abandons_in_flight_tool_without_partial_continuation`; `tests/test_l3s5_recovery_role_runtime.py::test_resume_before_llm_call_restarts_decision_from_persisted_boundary` |
| AC-017 | `tests/test_l3s5_actions_contract.py::test_action_contract_supports_complete_v1_set_and_rejects_extra_fields`; `tests/test_l3s5_actions_contract.py::test_invoke_role_accepts_registered_ids_and_validates_target_input` |
| AC-018 | `tests/test_l3s5_actions_contract.py::test_ownership_and_missing_entity_fail_before_mutation`; `tests/test_l3s5_actions_contract.py::test_role_action_and_tool_allowlists_are_runtime_enforced`; `tests/test_l3s5_actions_contract.py::test_invoke_role_accepts_registered_ids_and_validates_target_input` |
| AC-019 | `tests/test_l3s5_actions_contract.py::test_executor_delegates_and_prompt_changes_cannot_bypass_validation`; `tests/test_l3s5_actions_contract.py::test_retrieval_delegates_query_as_lower_layer_contract`; integration node above |
| AC-020 | `tests/test_l3s5_retry_role_runtime.py::test_provider_retry_does_not_increment_agentic_step`; `tests/test_l3s5_retry_role_runtime.py::test_structured_output_repair_does_not_increment_agentic_step` |
| AC-021 | `tests/test_l3s5_retry_role_runtime.py::test_llm_budget_exhaustion_returns_structured_termination`; `tests/test_l3s5_main_runtime_orchestration.py::test_main_runtime_limits_terminate_before_another_role`; `tests/test_l3s5_main_runtime_orchestration.py::test_cancellation_is_deterministic_without_invoking_a_role` |
| AC-022 | `tests/test_l3s5_retry_role_runtime.py::test_policy_failure_is_isolated_in_role_result`; `tests/test_l3s5_main_runtime_orchestration.py::test_main_max_failures_handles_role_failure_without_crashing_agent_run` |
| AC-023 | `tests/test_l3s5_trace_role_runtime.py::test_committed_action_and_snapshot_survive_later_role_failure` |
| AC-024 | `tests/test_l3s5_trace_role_runtime.py::test_role_events_use_agent_trace_shape_and_role_identity`; integration node above |
| AC-025 | `tests/test_l3s5_builtin_roles_contracts.py::test_all_five_roles_have_independent_contract_boundaries`; `tests/test_l3s5_builtin_roles_contracts.py::test_all_five_roles_project_the_same_valid_runtime_snapshot` |
| AC-026 | `tests/test_l3s5_builtin_roles_contracts.py::test_role_output_schemas_reject_cross_responsibility_fields`; `tests/test_l3s5_builtin_roles_contracts.py::test_action_and_tool_allowlists_enforce_narrow_responsibilities` |
| AC-027 | `tests/test_l3s5_actions_contract.py::test_executor_delegates_and_prompt_changes_cannot_bypass_validation` |
| AC-028 | `tests/test_l3s5_context_projection.py::test_omitted_context_has_no_role_input_access_path_or_memory_dependency` plus the full L3S5 suite import/run without Memory/Wiki promotion runtime dependencies |
| AC-029 | `tests/test_l3s5_main_runtime_orchestration.py::test_unregistered_role_selection_fails_structured_validation_and_never_executes`; `tests/test_l3s5_actions_contract.py::test_invoke_role_accepts_registered_ids_and_validates_target_input` |
| AC-030 | `tests/test_l3s5_role_contracts.py::test_main_and_role_budgets_are_independent_and_externally_overridable`; `tests/test_l3s5_builtin_roles_contracts.py::test_every_runtime_profile_is_externally_overridable`; `tests/test_l3s5_main_runtime_orchestration.py::test_main_runtime_limits_terminate_before_another_role` |
| AC-031 | `tests/integration/test_l3s5_end_to_end_runtime.py::test_database_backed_role_chain_persists_ledgers_trace_and_provenance` |

The AC-031 integration evidence asserts successful completion, exact predefined Role order,
runtime-created Query/Evidence/Claim/link records, a second Query cycle, Runtime/Role/action
AgentTrace lifecycle events, and a `FinalResponseArtifact` whose citations and enriched source
references preserve durable locator, metadata, and retrieval provenance.
