import json
from datetime import datetime, timezone

import pytest

from transit_scholar.layer3.actions.models import (
    AdmitEvidenceAction,
    CreateClaimAction,
    CreateQueryAction,
    LinkEvidenceAction,
    RetrieveQueryAction,
)
from transit_scholar.layer3.actions import ActionExecutor, ActionValidator
from transit_scholar.layer3.agent import RoleId, built_in_role_registry
from transit_scholar.layer3.context import RoleContext, RuntimeContextSnapshotBuilder
from transit_scholar.layer3.evidence import EvidenceLocator, ResearchEvidence
from transit_scholar.layer3.execution import AgentRunService
from transit_scholar.layer3.ledger import EvidenceRecord
from transit_scholar.layer3.ledger import ResearchReasoningLedgerService
from transit_scholar.layer3.retrieval import ResearchQuery
from transit_scholar.layer3.roles.builtin import (
    ClaimReasoningRole,
    EvidenceReasoningRole,
    FinalSynthesisRole,
    QueryPlanningRole,
    ResearchCoordinatorRole,
)
from transit_scholar.layer3.runtime import MainResearchRuntime, MainRuntimeConfig, RoleRuntime
from transit_scholar.layer3.tools import RetrievalResultEnvelope
from transit_scholar.layer3.workspace import WorkspaceService
from transit_scholar.product.roles import BuiltinRoleActionPlanner, StructuredLLMRolePolicy


def _context(role_id, **sections):
    return RoleContext(
        role_id=role_id.value,
        sections={
            "session": {
                "agent_run": {
                    "agent_run_id": "run-1",
                    "workspace_id": "workspace-1",
                },
                "research_session": {"research_session_id": "session-1"},
            },
            **sections,
        },
        omitted_sections=frozenset(),
        serialized_chars=2,
    )


def _evidence(evidence_id="evidence-1"):
    return {
        "evidence_id": evidence_id,
        "locator": {
            "workspace_id": "workspace-1",
            "source_kind": "paper",
            "paper_id": "paper-1",
        },
        "text": "Retrieved source text.",
        "source_kind": "paper",
        "query_provenance": {"query_id": "query-1", "session_id": "session-1"},
    }


def test_query_planning_emits_paired_actions_from_serialized_output():
    actions = BuiltinRoleActionPlanner().plan(
        QueryPlanningRole(),
        {"completed": True, "proposed_queries": ["transit reliability"]},
        _context(RoleId.QUERY_PLANNING),
    )

    assert len(actions) == 2
    assert isinstance(actions[0], CreateQueryAction)
    assert isinstance(actions[1], RetrieveQueryAction)
    assert actions[0].query_id == actions[1].query_id
    assert actions[0].workspace_id == "workspace-1"
    assert actions[0].agent_run_id == "run-1"
    assert actions[0].research_session_id == "session-1"


def test_evidence_reasoning_requires_retrieved_payload_identity():
    planner = BuiltinRoleActionPlanner()
    context = _context(
        RoleId.EVIDENCE_REASONING,
        retrieved_evidence=({"evidence_id": "evidence-1", "payload": _evidence()},),
    )

    actions = planner.plan(
        EvidenceReasoningRole(),
        {"completed": True, "admitted_evidence_ids": ["evidence-1"]},
        context,
    )

    assert len(actions) == 1
    assert isinstance(actions[0], AdmitEvidenceAction)
    assert actions[0].source_query_id == "query-1"
    with pytest.raises(ValueError, match="not retrieved"):
        planner.plan(
            EvidenceReasoningRole(),
            {"completed": True, "admitted_evidence_ids": ["unknown"]},
            context,
        )


def test_claim_reasoning_links_only_admitted_evidence():
    accepted = EvidenceRecord(
        evidence_id="evidence-1",
        research_session_id="session-1",
        source_query_id="query-1",
        locator=_evidence()["locator"],
        text_snapshot="Retrieved source text.",
        created_at=datetime.now(timezone.utc),
    )
    planner = BuiltinRoleActionPlanner()
    actions = planner.plan(
        ClaimReasoningRole(),
        {
            "completed": True,
            "proposed_claims": [{"statement": "Reliability improves.", "evidence_ids": ["evidence-1"]}],
        },
        _context(RoleId.CLAIM_REASONING, accepted_evidence=(accepted,)),
    )

    assert isinstance(actions[0], CreateClaimAction)
    assert isinstance(actions[1], LinkEvidenceAction)
    assert actions[0].claim_id == actions[1].claim_id
    with pytest.raises(ValueError, match="not admitted"):
        planner.plan(
            ClaimReasoningRole(),
            {"completed": True, "proposed_claims": [{"statement": "No basis.", "evidence_ids": ["other"]}]},
            _context(RoleId.CLAIM_REASONING, accepted_evidence=(accepted,)),
        )


@pytest.mark.parametrize("role, output", [
    (ResearchCoordinatorRole(), {"completed": True}),
    (FinalSynthesisRole(), {"completed": False, "answer_text": "Not finalized."}),
])
def test_non_specialist_roles_do_not_emit_mutation_actions(role, output):
    assert BuiltinRoleActionPlanner().plan(role, output, _context(role.role_id)) == ()


class _RoleChainLLM:
    is_fake = True
    provider_name = "test"
    model_name = "role-chain"

    def __init__(self):
        self.coordinator_calls = 0
        self.inputs = {}

    def generate_structured(self, messages, output_schema, metadata=None):
        payload = json.loads(messages[-1]["content"])
        role_id = RoleId(metadata["role_id"])
        self.inputs[role_id] = payload["role_input"]
        if role_id == RoleId.RESEARCH_COORDINATOR:
            next_roles = (
                RoleId.QUERY_PLANNING,
                RoleId.EVIDENCE_REASONING,
                RoleId.CLAIM_REASONING,
                RoleId.FINAL_SYNTHESIS,
            )
            result = {"completed": True, "next_role_id": next_roles[self.coordinator_calls]}
            self.coordinator_calls += 1
        elif role_id == RoleId.QUERY_PLANNING:
            result = {"completed": True, "proposed_queries": ["transit reliability"]}
        elif role_id == RoleId.EVIDENCE_REASONING:
            result = {
                "completed": True,
                "admitted_evidence_ids": payload["role_input"]["evidence_ids"],
            }
        elif role_id == RoleId.CLAIM_REASONING:
            evidence_id = payload["role_input"]["accepted_evidence_ids"][0]
            result = {
                "completed": True,
                "proposed_claims": [{
                    "statement": "Reliable service reduces passenger delay.",
                    "evidence_ids": [evidence_id],
                }],
            }
        else:
            result = {
                "completed": True,
                "answer_text": "Reliable service reduces passenger delay.",
                "citation_references": [
                    item["evidence_id"] for item in payload["role_input"]["accepted_evidence"]
                ],
            }
        return output_schema.model_validate(result)


def test_production_bridges_complete_session_role_chain(session):
    workspace = WorkspaceService(session).create(name="Production role chain").workspace
    execution = AgentRunService(session)
    run = execution.create_agent_run(workspace_id=workspace.workspace_id, user_goal="Assess reliability")
    research = execution.create_research_session(
        agent_run_id=run.agent_run_id, research_question="Does reliability reduce delay?"
    )
    ledger = ResearchReasoningLedgerService(session)

    class Knowledge:
        def retrieve_knowledge(self, query: ResearchQuery):
            evidence = ResearchEvidence(
                evidence_id="retrieved-evidence-1",
                locator=EvidenceLocator(
                    workspace_id=workspace.workspace_id,
                    source_kind="paper",
                    paper_id="paper-1",
                    parse_run_id="parse-1",
                ),
                text="A reliable transit service reduced passenger delay.",
                source_kind="paper",
                query_provenance={"query_id": query.query_id, "session_id": query.session_id},
                paper_provenance={"paper_id": "paper-1", "title": "Transit reliability"},
            )
            return RetrievalResultEnvelope(query=query, evidence_results=[evidence])

    registry = built_in_role_registry()
    validator = ActionValidator(
        execution_service=execution, ledger_service=ledger, role_registry=registry
    )
    executor = ActionExecutor(
        validator=validator,
        execution_service=execution,
        ledger_service=ledger,
        knowledge_service=Knowledge(),
        role_invoker=lambda *_: None,
    )
    llm = _RoleChainLLM()
    policy = StructuredLLMRolePolicy(llm)
    runtime = MainResearchRuntime(
        registry=registry,
        role_runtime=RoleRuntime(registry, action_executor=executor),
        execution_service=execution,
        context_builder=RuntimeContextSnapshotBuilder(session),
        policies={role_id: policy for role_id in RoleId},
        config=MainRuntimeConfig(max_steps=10, max_tool_calls=10),
        action_planner=BuiltinRoleActionPlanner(),
        action_executor=executor,
    )

    result = runtime.execute(
        agent_run_id=run.agent_run_id, research_session_id=research.research_session_id
    )

    assert result.status == "completed", result.failure_message
    assert [item.role_id for item in result.role_results] == [
        RoleId.RESEARCH_COORDINATOR,
        RoleId.QUERY_PLANNING,
        RoleId.RESEARCH_COORDINATOR,
        RoleId.EVIDENCE_REASONING,
        RoleId.RESEARCH_COORDINATOR,
        RoleId.CLAIM_REASONING,
        RoleId.RESEARCH_COORDINATOR,
        RoleId.FINAL_SYNTHESIS,
    ]
    assert llm.inputs[RoleId.EVIDENCE_REASONING]["evidence_ids"] == ["retrieved-evidence-1"]
    assert len(result.role_results[3].working_state.intermediate_artifacts) == 1
    queries = ledger.list_queries(research_session_id=research.research_session_id)
    evidence = ledger.list_evidence(research_session_id=research.research_session_id)
    claims = ledger.list_claims(research_session_id=research.research_session_id)
    assert len(queries) == len(evidence) == len(claims) == 1
    assert evidence[0].source_query_id == queries[0].query_id
    links = ledger.get_claim_evidence(
        research_session_id=research.research_session_id, claim_id=claims[0].claim_id
    )
    assert [link.evidence_id for link in links] == [evidence[0].evidence_id]
    assert result.final_response is not None
    assert result.final_response.citation_references == [evidence[0].evidence_id]
