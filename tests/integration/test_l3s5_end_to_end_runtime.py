"""Deterministic L3S5 acceptance path over persisted lower-layer services."""

from transit_scholar.layer3.actions import (
    ActionExecutor,
    ActionValidator,
    AdmitEvidenceAction,
    CreateClaimAction,
    CreateQueryAction,
    LinkEvidenceAction,
    RetrieveQueryAction,
)
from transit_scholar.layer3.agent import RoleId, RoleRegistry, RoleRuntimeProfile, built_in_role_registry
from transit_scholar.layer3.context import RuntimeContextSnapshotBuilder
from transit_scholar.layer3.evidence import EvidenceLocator, ResearchEvidence
from transit_scholar.layer3.execution import AgentRunService
from transit_scholar.layer3.ledger import ResearchReasoningLedgerService
from transit_scholar.layer3.runtime import MainResearchRuntime, MainRuntimeConfig, RoleRuntime
from transit_scholar.layer3.tools import RetrievalResultEnvelope
from transit_scholar.layer3.retrieval import ResearchQuery
from transit_scholar.layer3.trace import AgentTraceService
from transit_scholar.layer3.workspace import WorkspaceService


class _Policy:
    def __init__(self):
        self.calls = {role: 0 for role in RoleId}

    def decide(self, definition, role_input, state):
        self.calls[definition.role_id] += 1
        if definition.role_id == RoleId.RESEARCH_COORDINATOR:
            sequence = [
                "query_planning", "evidence_reasoning", "claim_reasoning",
                "query_planning", "evidence_reasoning", "claim_reasoning",
                "final_synthesis",
            ]
            return {"completed": True, "next_role_id": sequence[self.calls[definition.role_id] - 1]}
        if definition.role_id == RoleId.QUERY_PLANNING:
            query_number = self.calls[definition.role_id]
            return {"completed": True, "proposed_queries": [f"intervention delay evidence {query_number}"]}
        if definition.role_id == RoleId.EVIDENCE_REASONING:
            return {"completed": True, "admitted_evidence_ids": list(role_input.evidence_ids)}
        if definition.role_id == RoleId.CLAIM_REASONING:
            evidence_id = role_input.accepted_evidence_ids[-1]
            return {
                "completed": True,
                "proposed_claims": [{
                    "statement": f"Accepted evidence {evidence_id} supports reduced delay.",
                    "evidence_ids": [evidence_id],
                }],
            }
        return {
            "completed": True,
            "answer_text": "The intervention reduces delay across both retrieved sources.",
            "citation_references": [item.evidence_id for item in role_input.accepted_evidence],
        }


def test_database_backed_role_chain_persists_ledgers_trace_and_provenance(session):
    workspace = WorkspaceService(session).create(name="L3S5 e2e").workspace
    execution = AgentRunService(session)
    run = execution.create_agent_run(workspace_id=workspace.workspace_id, user_goal="Assess intervention")
    research = execution.create_research_session(
        agent_run_id=run.agent_run_id, research_question="Does it reduce delay?"
    )
    ledger = ResearchReasoningLedgerService(session)
    assert ledger.list_queries(research_session_id=research.research_session_id) == []
    assert ledger.list_evidence(research_session_id=research.research_session_id) == []
    assert ledger.list_claims(research_session_id=research.research_session_id) == []

    evidence_by_query = {
        "query-1": ResearchEvidence(
            evidence_id="evidence-1",
            locator=EvidenceLocator(
                workspace_id=workspace.workspace_id, source_kind="paper",
                paper_id="paper-1", block_id="block-1",
            ),
            text="The first study observed reduced delay.", source_kind="paper",
            retrieval_provenance={"provider": "deterministic-stub", "rank": 1},
        ),
        "query-2": ResearchEvidence(
            evidence_id="evidence-2",
            locator=EvidenceLocator(
                workspace_id=workspace.workspace_id, source_kind="paper",
                paper_id="paper-2", block_id="block-2",
            ),
            text="The follow-up study confirmed reduced delay.", source_kind="paper",
            retrieval_provenance={"provider": "deterministic-stub", "rank": 1},
        ),
    }

    class Knowledge:
        def retrieve_knowledge(self, query):
            return RetrievalResultEnvelope(
                query=ResearchQuery.model_validate(query),
                evidence_results=[evidence_by_query[query.query_id]],
            )

    base_registry = built_in_role_registry({
        role: RoleRuntimeProfile(max_steps=1, max_tool_calls=5) for role in RoleId
    })
    registry = RoleRegistry([
        definition.model_copy(update={
            "allowed_actions": definition.allowed_actions | {"RETRIEVE_QUERY"},
            "allowed_tools": definition.allowed_tools | {"retrieve_knowledge"},
        }) if definition.role_id == RoleId.QUERY_PLANNING else definition
        for definition in base_registry.list()
    ])
    validator = ActionValidator(
        execution_service=execution, ledger_service=ledger, role_registry=registry
    )
    executor = ActionExecutor(
        validator=validator, execution_service=execution, ledger_service=ledger,
        knowledge_service=Knowledge(), role_invoker=lambda *_: None,
    )
    trace = AgentTraceService(session)
    common = {
        "workspace_id": workspace.workspace_id,
        "agent_run_id": run.agent_run_id,
        "research_session_id": research.research_session_id,
    }

    def planner(role, output, context):
        if role.role_id == RoleId.QUERY_PLANNING:
            query_number = len(context.sections.get("queries", [])) + 1
            query_id = f"query-{query_number}"
            return [
                CreateQueryAction(**common, query_id=query_id, query_text=output["proposed_queries"][0]),
                RetrieveQueryAction(**common, query_id=query_id),
            ]
        if role.role_id == RoleId.EVIDENCE_REASONING:
            admitted_ids = set(output["admitted_evidence_ids"])
            return [
                AdmitEvidenceAction(
                    **common,
                    source_query_id=f"query-{item['evidence_id'].split('-')[-1]}",
                    evidence=item["payload"],
                )
                for item in context.sections["retrieved_evidence"]
                if item["evidence_id"] in admitted_ids
            ]
        if role.role_id == RoleId.CLAIM_REASONING:
            claim_number = len(context.sections.get("claims", [])) + 1
            claim_id = f"claim-{claim_number}"
            proposal = output["proposed_claims"][0]
            return [
                CreateClaimAction(
                    **common, claim_id=claim_id, statement=proposal["statement"], status="supported"
                ),
                *[
                    LinkEvidenceAction(
                        **common, claim_id=claim_id, evidence_id=evidence_id, relation="supports"
                    )
                    for evidence_id in proposal["evidence_ids"]
                ],
            ]
        return []

    policy = _Policy()
    runtime = MainResearchRuntime(
        registry=registry, role_runtime=RoleRuntime(registry, trace=trace),
        execution_service=execution, context_builder=RuntimeContextSnapshotBuilder(session),
        policies={role: policy for role in RoleId},
        config=MainRuntimeConfig(max_steps=20, max_tool_calls=20), trace=trace,
        action_planner=planner, action_executor=executor,
    )
    result = runtime.execute(
        agent_run_id=run.agent_run_id, research_session_id=research.research_session_id
    )

    assert result.status == "completed", (
        result.failure_message,
        [(item.role_id, item.status, item.failure_message) for item in result.role_results],
    )
    assert [item.role_id for item in result.role_results] == [
        RoleId.RESEARCH_COORDINATOR, RoleId.QUERY_PLANNING,
        RoleId.RESEARCH_COORDINATOR, RoleId.EVIDENCE_REASONING,
        RoleId.RESEARCH_COORDINATOR, RoleId.CLAIM_REASONING,
        RoleId.RESEARCH_COORDINATOR, RoleId.QUERY_PLANNING,
        RoleId.RESEARCH_COORDINATOR, RoleId.EVIDENCE_REASONING,
        RoleId.RESEARCH_COORDINATOR, RoleId.CLAIM_REASONING,
        RoleId.RESEARCH_COORDINATOR, RoleId.FINAL_SYNTHESIS,
    ]
    queries = ledger.list_queries(research_session_id=research.research_session_id)
    admitted = ledger.list_evidence(research_session_id=research.research_session_id)
    claims = ledger.list_claims(research_session_id=research.research_session_id)
    assert [item.query_id for item in queries] == ["query-1", "query-2"]
    assert {item.source_query_id for item in admitted} == {"query-1", "query-2"}
    assert [item.claim_id for item in claims] == ["claim-1", "claim-2"]
    links = {
        (link.claim_id, link.evidence_id)
        for claim in claims
        for link in ledger.get_claim_evidence(
            research_session_id=research.research_session_id, claim_id=claim.claim_id
        )
    }
    assert {claim_id for claim_id, _ in links} == {"claim-1", "claim-2"}
    assert {evidence_id for _, evidence_id in links} <= {
        item.evidence_id for item in admitted
    }

    assert result.final_response is not None
    assert result.final_response.completed is True
    admitted_ids = {item.evidence_id for item in admitted}
    assert set(result.final_response.citation_references) == admitted_ids
    assert {source.evidence_id for source in result.final_response.source_references} == admitted_ids
    first_source = next(
        source for source in result.final_response.source_references
        if source.locator.paper_id == "paper-1"
    )
    assert first_source.locator.paper_id == "paper-1"
    assert first_source.source_metadata["source_kind"] == "paper"
    assert (
        first_source.retrieval_provenance["retrieval_provenance"]["provider"]
        == "deterministic-stub"
    )

    events = trace.read_trace(
        agent_run_id=run.agent_run_id, research_session_id=research.research_session_id
    )
    event_types = [event.event_type for event in events]
    assert event_types[0] == "runtime.start"
    assert event_types[-1] == "runtime.completion"
    assert event_types.count("role.start") == len(result.role_results)
    assert event_types.count("role.step") == len(result.role_results)
    assert event_types.count("role.result") == len(result.role_results)
    assert event_types.count("role.completion") == len(result.role_results)
    assert event_types.count("runtime.action") == 10
    assert all(
        event.payload.get("role_execution_id") and event.payload.get("role_id")
        for event in events if event.event_type.startswith("role.")
    )
