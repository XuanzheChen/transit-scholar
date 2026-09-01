"""Acceptance coverage for the design-time built-in research Roles."""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from transit_scholar.layer3.agent import RoleId, built_in_role_registry
from transit_scholar.layer3.context import (
    RoleContextProjector,
    RuntimeContextSnapshot,
    SessionContext,
)
from transit_scholar.layer3.execution import AgentRunRecord, ResearchSessionRecord
from transit_scholar.layer3.grounding import (
    GroundedWorkspace,
    SchemaCoverage,
    WorkspaceCapabilities,
)
from transit_scholar.layer3.roles import (
    BuiltinRoleRuntimeConfig,
    ClaimReasoningRole,
    EvidenceReasoningRole,
    FinalSynthesisRole,
    QueryPlanningRole,
    ResearchCoordinatorRole,
)
from transit_scholar.layer3.wiki import WorkspaceWikiStatus


def _valid_snapshot() -> RuntimeContextSnapshot:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return RuntimeContextSnapshot(
        session=SessionContext(
            agent_run=AgentRunRecord(
                agent_run_id="run-1",
                workspace_id="workspace-1",
                user_goal="Study transit control",
                status="running",
                workspace_revision=1,
                created_at=now,
                updated_at=now,
            ),
            research_session=ResearchSessionRecord(
                research_session_id="session-1",
                agent_run_id="run-1",
                research_question="Which control method works?",
                status="running",
                created_at=now,
                updated_at=now,
            ),
        ),
        workspace=GroundedWorkspace(
            workspace_id="workspace-1",
            name="Workspace",
            revision=1,
            status="active",
            schema_mode="none",
            schema_coverage=SchemaCoverage(
                workspace_id="workspace-1", total=0, ready=0, missing=0, status="disabled"
            ),
            base_wiki=WorkspaceWikiStatus(workspace_id="workspace-1", status="unsupported"),
            capabilities=WorkspaceCapabilities(
                workspace_id="workspace-1",
                knowledge_access=True,
                paper_access=True,
                evidence_access=False,
                schema_read=False,
                schema_materialization=False,
                wiki_build=False,
                wiki_read=False,
                evidence_ready_papers=0,
                schema_ready_papers=0,
            ),
        ),
    )


def test_all_five_roles_have_independent_contract_boundaries():
    registry = built_in_role_registry()
    roles = registry.list()

    assert {type(role) for role in roles} == {
        ResearchCoordinatorRole,
        QueryPlanningRole,
        EvidenceReasoningRole,
        ClaimReasoningRole,
        FinalSynthesisRole,
    }
    assert len({role.prompt_template for role in roles}) == 5
    assert len({role.output_contract for role in roles}) == 5
    assert len({role.context_policy for role in roles}) == 5
    assert len({id(role.runtime_profile) for role in roles}) == 5


def test_all_five_roles_project_the_same_valid_runtime_snapshot():
    snapshot = _valid_snapshot()
    projector = RoleContextProjector()

    projected = {
        role.role_id: projector.project(snapshot, role)
        for role in built_in_role_registry().list()
    }

    assert set(projected) == set(RoleId)
    assert set(projected[RoleId.RESEARCH_COORDINATOR].sections) == {
        "session",
        "queries",
        "retrieved_evidence",
        "accepted_evidence",
        "claims",
    }


def test_role_output_schemas_reject_cross_responsibility_fields():
    registry = built_in_role_registry()

    with pytest.raises(ValidationError):
        registry.get(RoleId.QUERY_PLANNING).output_contract.model_validate(
            {"proposed_claims": []}
        )
    with pytest.raises(ValidationError):
        registry.get(RoleId.CLAIM_REASONING).output_contract.model_validate(
            {"proposed_queries": ["outside boundary"]}
        )


def test_action_and_tool_allowlists_enforce_narrow_responsibilities():
    registry = built_in_role_registry()
    query = registry.get(RoleId.QUERY_PLANNING)
    claim = registry.get(RoleId.CLAIM_REASONING)

    assert query.allowed_actions == {"CREATE_QUERY", "UPDATE_QUERY"}
    assert not query.allowed_actions & {"CREATE_CLAIM", "UPDATE_CLAIM", "LINK_EVIDENCE"}
    assert claim.allowed_actions == {"CREATE_CLAIM", "UPDATE_CLAIM", "LINK_EVIDENCE"}
    assert not claim.allowed_actions & {"CREATE_QUERY", "UPDATE_QUERY"}
    assert all(not role.allowed_tools for role in registry.list())


def test_every_runtime_profile_is_externally_overridable():
    values = {
        role_id.value: {
            "max_steps": index + 1,
            "max_llm_calls": index + 2,
            "max_tool_calls": index,
            "max_failures": index,
            "provider_retry_limit": index,
            "structured_output_repair_limit": index,
        }
        for index, role_id in enumerate(RoleId)
    }
    config = BuiltinRoleRuntimeConfig.model_validate(values)
    registry = built_in_role_registry(values)

    for index, role_id in enumerate(RoleId):
        assert config.for_role(role_id).max_steps == index + 1
        assert registry.get(role_id).runtime_profile == config.for_role(role_id)
