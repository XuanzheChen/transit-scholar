"""Layer3 Stage5 Role contract acceptance tests."""

import pytest

from transit_scholar.layer3.agent import (
    QueryPlanningOutput,
    RoleRuntimeProfile,
    UnregisteredRoleError,
    built_in_role_registry,
)
from transit_scholar.layer3.runtime import MainRuntimeConfig, RoleRuntime
from transit_scholar.layer3.context import RoleContext


def _context(role_id="query_planning"):
    return RoleContext(role_id=role_id, sections={}, omitted_sections=frozenset(), serialized_chars=2)


class CompletingPolicy:
    def __init__(self, complete_on: int) -> None:
        self.complete_on = complete_on

    def decide(self, definition, role_input, state, role_context, repair_context=None):
        return QueryPlanningOutput(
            completed=state.current_step + 1 >= self.complete_on,
            proposed_queries=[role_input.research_question],
        )


def test_contracts_and_runtime_configuration_serialize_without_secrets():
    registry = built_in_role_registry()
    serialized = registry.get("query_planning").model_dump_json()
    main_serialized = MainRuntimeConfig(max_steps=2).model_dump_json()

    assert "QueryPlanningInput" in serialized
    assert "api_key" not in serialized + main_serialized


def test_registry_rejects_unregistered_roles():
    with pytest.raises(UnregisteredRoleError):
        built_in_role_registry().get("invented_role")


def test_main_and_role_budgets_are_independent_and_externally_overridable():
    main = MainRuntimeConfig(max_steps=1, max_llm_calls=1)
    registry = built_in_role_registry(
        {"query_planning": RoleRuntimeProfile(max_steps=3, max_llm_calls=3)}
    )
    role = registry.get("query_planning")

    assert main.max_steps == 1
    assert role.runtime_profile.max_steps == 3
    result = RoleRuntime(registry).execute(
        role,
        {"research_session_id": "session-1", "research_question": "Question"},
        CompletingPolicy(3),
        agent_run_id="run-1",
        research_session_id="session-1",
        role_context=_context(),
    )
    assert result.status == "completed"
    assert result.working_state.current_step == 3


def test_same_runtime_contract_supports_one_and_multiple_steps():
    one_registry = built_in_role_registry(
        {"query_planning": {"max_steps": 1, "max_llm_calls": 1}}
    )
    multi_registry = built_in_role_registry(
        {"query_planning": {"max_steps": 2, "max_llm_calls": 2}}
    )

    def execute(registry, complete_on):
        return RoleRuntime(registry).execute(
            registry.get("query_planning"),
            {"research_session_id": "session-1", "research_question": "Question"},
            CompletingPolicy(complete_on),
            agent_run_id="run-1",
            research_session_id="session-1",
            role_context=_context(),
        )

    assert execute(one_registry, 1).working_state.current_step == 1
    assert execute(multi_registry, 2).working_state.current_step == 2
