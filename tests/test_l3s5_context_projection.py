"""Role-specific context governance acceptance tests."""

from datetime import datetime, timezone
import json

import pytest

from transit_scholar.layer3.agent import ContextPolicy, built_in_role_registry
from transit_scholar.layer3.runtime import RoleRuntime
from transit_scholar.layer3.context import (
    ContextBudgetExceededError,
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
from transit_scholar.layer3.ledger import ClaimRecord, ResearchQueryRecord
from transit_scholar.layer3.wiki import WorkspaceWikiStatus


def _snapshot() -> RuntimeContextSnapshot:
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
        queries=(
            ResearchQueryRecord(
                query_id="query-1",
                research_session_id="session-1",
                query_text="adaptive bus control",
                status="active",
                created_at=now,
                updated_at=now,
            ),
        ),
        claims=(
            ClaimRecord(
                claim_id="claim-1",
                research_session_id="session-1",
                statement="A durable claim invisible to query planning",
                status="proposed",
                created_at=now,
                updated_at=now,
            ),
        ),
    )


def test_same_snapshot_projects_observably_different_role_contexts():
    snapshot = _snapshot()
    registry = built_in_role_registry()
    projector = RoleContextProjector()

    query = projector.project(snapshot, registry.get("query_planning"))
    evidence = projector.project(snapshot, registry.get("evidence_reasoning"))
    claim = projector.project(snapshot, registry.get("claim_reasoning"))

    assert set(query.sections) == {"queries", "session"}
    assert set(evidence.sections) == {"session", "queries", "retrieved_evidence"}
    assert set(claim.sections) == {"session", "accepted_evidence", "claims"}
    assert query.model_dump_json() == projector.project(
        snapshot, registry.get("query_planning")
    ).model_dump_json()


def test_omitted_context_has_no_role_input_access_path_or_memory_dependency():
    context = RoleContextProjector().project(
        _snapshot(), built_in_role_registry().get("query_planning")
    )

    assert "claims" not in context.sections
    assert "claim-1" not in json.dumps(context.sections)
    assert not hasattr(context, "snapshot")
    with pytest.raises(KeyError):
        context.require("claims")


def test_projection_serialization_and_limits_are_deterministic():
    snapshot = _snapshot()
    role = built_in_role_registry().get("query_planning")
    limited_role = role.model_copy(
        update={
            "context_policy": ContextPolicy(
                included_sections={"queries"}, max_items_per_section=1
            )
        }
    )
    context = RoleContextProjector().project(snapshot, limited_role)

    assert len(context.sections["queries"]) == 1
    assert context.serialized_chars == len(
        json.dumps(
            context.sections,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    assert RuntimeContextSnapshot.model_validate_json(snapshot.model_dump_json()) == snapshot

    too_small = role.model_copy(
        update={
            "context_policy": ContextPolicy(
                included_sections={"session"}, max_serialized_chars=2
            )
        }
    )
    with pytest.raises(ContextBudgetExceededError):
        RoleContextProjector().project(snapshot, too_small)


def test_projected_context_reaches_semantic_policy_decision():
    registry = built_in_role_registry()
    role = registry.get("query_planning")
    context = RoleContextProjector().project(_snapshot(), role)

    class Policy:
        def decide(self, definition, role_input, state, role_context):
            assert role_context is context
            assert role_context.sections["queries"][0]["query_text"] == "adaptive bus control"
            assert "claims" not in role_context.sections
            return {"completed": True, "proposed_queries": ["refined"]}

    result = RoleRuntime(registry).execute(
        role,
        {"research_session_id": "session-1", "research_question": "Question"},
        Policy(),
        agent_run_id="run-1",
        research_session_id="session-1",
        role_context=context,
    )

    assert result.status == "completed"
