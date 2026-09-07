"""P1 product-composition acceptance: two related turns through Agent Core."""

from transit_scholar.db.engine import SessionLocal
from transit_scholar.db.models import AgentRun, Paper, WorkspacePaperMembership
from transit_scholar.layer3.agent import (
    ClaimReasoningOutput, EvidenceReasoningOutput, FinalSynthesisOutput,
    QueryPlanningOutput, RoleId, built_in_role_registry,
)
from transit_scholar.layer3.evidence import EvidenceLocator, PaperProvenance, QueryProvenance, ResearchEvidence
from transit_scholar.layer3.run_context import RunRuntimeConfig
from transit_scholar.layer3.planning import RunDecision
from transit_scholar.layer3.tools import RetrievalResultEnvelope
from transit_scholar.layer3.workspace import WorkspaceService
from transit_scholar.product.conversation import ConversationGoalResolver
from transit_scholar.product.facade import TransitScholarProduct
from transit_scholar.product.runtime import RuntimeFactory


class DeterministicKnowledge:
    def retrieve_knowledge(self, query):
        evidence = ResearchEvidence(
            evidence_id=f"evidence:{query.session_id}",
            locator=EvidenceLocator(workspace_id=query.workspace_id, source_kind="paper", paper_id="paper-1", block_id="block-1"),
            text="Transit demand forecasting is improved by the fixture method.",
            source_kind="rag",
            query_provenance=QueryProvenance(query_id=query.query_id, session_id=query.session_id, query_text=query.query_text),
            paper_provenance=PaperProvenance(paper_id="paper-1", title="Transit demand forecasting"),
        )
        return RetrievalResultEnvelope(query=query, evidence_results=[evidence], workspace_revision=1)


class DeterministicPolicy:
    def __init__(self):
        self.coordinator_calls = {}

    def decide(self, definition, role_input, state, role_context, repair_context=None):
        role_id = definition.role_id
        if role_id == RoleId.RESEARCH_COORDINATOR:
            session_id = role_input.research_session_id
            count = self.coordinator_calls.get(session_id, 0)
            self.coordinator_calls[session_id] = count + 1
            next_roles = [RoleId.QUERY_PLANNING, RoleId.EVIDENCE_REASONING, RoleId.CLAIM_REASONING, RoleId.FINAL_SYNTHESIS]
            return {"next_role_id": next_roles[count] if count < len(next_roles) else None}
        if role_id == RoleId.QUERY_PLANNING:
            return QueryPlanningOutput(completed=True, proposed_queries=[role_input.research_question])
        if role_id == RoleId.EVIDENCE_REASONING:
            evidence = role_context.sections.get("retrieved_evidence", ())
            ids = [item.evidence_id if hasattr(item, "evidence_id") else item["evidence_id"] for item in evidence]
            return EvidenceReasoningOutput(completed=True, admitted_evidence_ids=ids)
        if role_id == RoleId.CLAIM_REASONING:
            accepted = role_context.sections.get("accepted_evidence", ())
            ids = [item.evidence_id if hasattr(item, "evidence_id") else item["evidence_id"] for item in accepted]
            return ClaimReasoningOutput(completed=True, proposed_claims=[{"statement": "The fixture method improves transit demand forecasting.", "evidence_ids": ids}])
        return FinalSynthesisOutput(completed=True, answer_text=f"Grounded answer for {role_input.research_session_id}")


def test_two_related_turns_are_independent_runs_with_product_owned_history(session, project_tmp_path):
    workspace = WorkspaceService(session).create(name="two-turn fixture").workspace
    paper = Paper(title="Transit demand forecasting", normalized_title="transit demand forecasting")
    session.add(paper)
    session.flush()
    session.add(WorkspacePaperMembership(workspace_id=workspace.workspace_id, paper_id=paper.id))
    session.commit()

    policy = DeterministicPolicy()
    registry = built_in_role_registry()
    def coordinator(snapshot):
        if snapshot.session_outcomes:
            return RunDecision(mode="complete", completion_reason="fixture research complete")
        return RunDecision(mode="direct_session", proposed_questions=[snapshot.user_goal])
    factory = RuntimeFactory(
        session_factory=SessionLocal,
        data_root=project_tmp_path,
        runtime_root=project_tmp_path / "layer3" / "runs",
        role_registry=registry,
        policies={definition.role_id: policy for definition in registry.list()},
        coordinator=coordinator,
        knowledge_service=DeterministicKnowledge(),
        l3s7_lifecycle=type("Lifecycle", (), {
            "episodic_store": object(),
            "configure_authoritative_readers": lambda self, **_: None,
            "maintain_before_session": lambda self, **_: None,
            "complete_agent_run": lambda self, **_: None,
        })(),
        episodic_memory=object(),
        run_config=RunRuntimeConfig(max_episodic_memory_candidates=0),
    )

    seen_context = []
    def resolve(message, prior_turns):
        seen_context.append([turn.user_message for turn in prior_turns])
        return message if not prior_turns else "Summarize the transit demand forecasting paper identified in Turn 1"

    product = TransitScholarProduct(session, factory, goal_resolver=ConversationGoalResolver(resolve))
    conversation = product.create_conversation(workspace.workspace_id, title="Research")
    first = product.submit_message(conversation.id, "Find the transit demand forecasting paper")
    second = product.submit_message(conversation.id, "Summarize that paper")

    assert first.status == second.status == "completed"
    assert first.agent_run_id != second.agent_run_id
    assert first.resolved_user_goal == "Find the transit demand forecasting paper"
    assert "transit demand forecasting" in second.resolved_user_goal
    assert seen_context == [[first.user_message]]
    assert first.final_assistant_response["answer_text"]
    assert second.final_assistant_response["answer_text"]
    runs = session.query(AgentRun).filter_by(workspace_id=workspace.workspace_id).all()
    assert {run.id for run in runs} == {first.agent_run_id, second.agent_run_id}
    assert all(run.status == "completed" for run in runs)
    view = product.read_conversation(conversation.id)
    assert [turn["sequence"] for turn in view["turns"]] == [1, 2]
    assert view["turns"][1]["run_state"].workspace_id == workspace.workspace_id
    assert view["turns"][1]["run_state"].phase == "completed"
