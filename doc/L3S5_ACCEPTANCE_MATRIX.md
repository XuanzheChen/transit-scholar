# L3S5 v3 Acceptance Evidence Matrix

Each criterion maps to executable pytest evidence. Focused v3 regression tests
cover RoleContext, repair feedback, persisted profiles, and in-flight recovery.

| AC | Executable evidence |
|---|---|
| AC-001 | `tests/integration/test_l3s5_end_to_end_runtime.py::test_database_backed_role_chain_persists_ledgers_trace_and_provenance` |
| AC-002 | `tests/test_l3s5_builtin_roles_contracts.py::test_all_five_roles_have_independent_contract_boundaries` |
| AC-003 | `tests/test_l3s5_role_contracts.py::test_registry_rejects_unregistered_roles` |
| AC-004 | `tests/test_l3s5_role_contracts.py::test_same_runtime_contract_supports_one_and_multiple_steps` |
| AC-005 | `tests/test_l3s5_role_execution_persistence.py::test_role_execution_can_be_reloaded_with_working_state_and_usage` |
| AC-006 | `tests/test_l3s5_role_contracts.py::test_main_and_role_budgets_are_independent_and_externally_overridable` |
| AC-007 | `tests/test_l3s5_builtin_roles_contracts.py::test_every_runtime_profile_is_externally_overridable` |
| AC-008 | `tests/test_l3s5_role_contracts.py::test_contracts_and_runtime_configuration_serialize_without_secrets` |
| AC-009 | `tests/test_l3s5_role_contracts.py::test_same_runtime_contract_supports_one_and_multiple_steps` |
| AC-010 | `tests/test_l3s5_final_synthesis_artifact.py::test_unstructured_or_malformed_final_output_fails_schema_validation` |
| AC-011 | `tests/test_l3s5_final_synthesis_artifact.py::test_final_synthesis_returns_artifact_with_durable_source_provenance` |
| AC-012 | `tests/test_l3s5_context_projection.py::test_same_snapshot_projects_observably_different_role_contexts` |
| AC-013 | `tests/test_l3s5_retry_role_runtime.py::test_role_runtime_requires_a_valid_projected_role_context`; `tests/test_l3s5_retry_role_runtime.py::test_legacy_policy_without_role_context_is_not_core_conforming` |
| AC-014 | `tests/test_l3s5_context_projection.py::test_query_planning_policy_reads_query_history_from_role_context` |
| AC-015 | `tests/test_l3s5_context_projection.py::test_evidence_reasoning_policy_reads_envelope_evidence_text_and_provenance` |
| AC-016 | `tests/test_l3s5_context_projection.py::test_claim_reasoning_policy_reads_accepted_evidence_text_and_existing_claims` |
| AC-017 | `tests/test_l3s5_context_projection.py::test_omitted_context_has_no_role_input_access_path_or_memory_dependency` |
| AC-018 | `tests/test_l3s5_role_execution_persistence.py::test_role_execution_can_be_reloaded_with_working_state_and_usage` |
| AC-019 | `tests/test_l3s5_recovery_role_runtime.py::test_resume_after_committed_action_does_not_replay_mutation` |
| AC-020 | `tests/test_l3s5_recovery_role_runtime.py::test_resume_before_llm_call_restarts_decision_from_persisted_boundary` |
| AC-021 | `tests/test_l3s5_actions_contract.py::test_action_contract_supports_complete_v1_set_and_rejects_extra_fields` |
| AC-022 | `tests/test_l3s5_actions_contract.py::test_ownership_and_missing_entity_fail_before_mutation` |
| AC-023 | `tests/test_l3s5_actions_contract.py::test_executor_delegates_and_prompt_changes_cannot_bypass_validation` |
| AC-024 | `tests/test_l3s5_retry_role_runtime.py::test_provider_retry_does_not_increment_agentic_step`; `tests/test_l3s5_retry_role_runtime.py::test_structured_output_repair_does_not_increment_agentic_step` |
| AC-025 | `tests/test_l3s5_retry_role_runtime.py::test_structured_output_repair_receives_validation_feedback` |
| AC-026 | `tests/test_l3s5_retry_role_runtime.py::test_legacy_policy_without_role_context_is_not_core_conforming` |
| AC-027 | `tests/test_l3s5_retry_role_runtime.py::test_llm_budget_exhaustion_returns_structured_termination` |
| AC-028 | `tests/test_l3s5_retry_role_runtime.py::test_policy_failure_is_isolated_in_role_result` |
| AC-029 | `tests/test_l3s5_trace_role_runtime.py::test_committed_action_and_snapshot_survive_later_role_failure` |
| AC-030 | `tests/test_l3s5_trace_role_runtime.py::test_role_events_use_agent_trace_shape_and_role_identity` |
| AC-031 | `tests/test_l3s5_builtin_roles_contracts.py::test_all_five_roles_have_independent_contract_boundaries` |
| AC-032 | `tests/test_l3s5_actions_contract.py::test_executor_delegates_and_prompt_changes_cannot_bypass_validation` |
| AC-033 | `tests/test_l3s5_context_projection.py::test_omitted_context_has_no_role_input_access_path_or_memory_dependency` |
| AC-034 | `tests/test_l3s5_role_contracts.py::test_registry_rejects_unregistered_roles` |
| AC-035 | `tests/test_l3s5_trace_role_runtime.py::test_role_executes_two_actions_before_completion_and_counts_both` |
| AC-036 | `tests/test_l3s5_trace_role_runtime.py::test_zero_role_tool_budget_blocks_action_before_mutation`; `tests/test_l3s5_main_runtime_orchestration.py::test_main_consumes_role_action_artifacts_without_executing_role_actions` |
| AC-037 | `tests/test_l3s5_trace_role_runtime.py::test_role_events_use_agent_trace_shape_and_role_identity` |
| AC-038 | `tests/integration/test_l3s5_end_to_end_runtime.py::test_database_backed_role_chain_persists_ledgers_trace_and_provenance` |
| AC-039 | `tests/test_l3s5_recovery_main_runtime.py::test_resume_does_not_replay_action_committed_before_main_boundary` |
| AC-040 | `tests/test_l3s5_recovery_main_runtime.py::test_resume_does_not_replay_action_committed_before_main_boundary` |
| AC-041 | `tests/test_l3s5_recovery_main_runtime.py::test_resume_does_not_replay_action_committed_before_main_boundary` |
| AC-042 | `tests/test_l3s5_recovery_role_runtime.py::test_recovery_uses_persisted_max_failures_for_failure_classification` |
| AC-043 | `tests/test_l3s5_recovery_role_runtime.py::test_resume_abandons_in_flight_tool_without_partial_continuation` |
