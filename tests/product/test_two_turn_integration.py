"""P1 product-composition acceptance: two related turns through Agent Core."""

from transit_scholar.db.engine import SessionLocal
from transit_scholar.db.models import AgentRun, EvidenceRecord, Paper, WorkspacePaperMembership
from transit_scholar.layer3.execution import AgentRunService
from transit_scholar.layer3.planning import RunDecision
from transit_scholar.layer3.run_context import RunRuntimeConfig
from transit_scholar.layer3.workspace import WorkspaceService
from transit_scholar.product.facade import TransitScholarProduct
from transit_scholar.product.runtime import RuntimeFactory
from transit_scholar.product.conversation import ConversationGoalResolver


def test_two_related_turns_are_independent_runs_with_product_owned_history(
    session, project_tmp_path
):
    workspace = WorkspaceService(session).create(name="two-turn fixture").workspace
    paper = Paper(title="Transit demand forecasting", normalized_title="transit demand forecasting")
    session.add(paper)
    session.flush()
    session.add(WorkspacePaperMembership(workspace_id=workspace.workspace_id, paper_id=paper.id))
    session.commit()

    def coordinator(_snapshot):
        return RunDecision(mode="complete", completion_reason="fixture complete")

    def synthesis(snapshot):
        return {"answer_text": f"Answer for: {snapshot.user_goal}"}

    factory = RuntimeFactory(
        session_factory=SessionLocal,
        data_root=project_tmp_path,
        runtime_root=project_tmp_path / "layer3" / "runs",
        coordinator=coordinator,
        synthesis=synthesis,
        l3s7_lifecycle=type("Lifecycle", (), {
            "episodic_store": object(),
            "configure_authoritative_readers": lambda self, **_: None,
            "maintain_before_session": lambda self, **_: None,
            "complete_agent_run": lambda self, **_: None,
        })(),
        episodic_memory=object(),
        run_config=RunRuntimeConfig(max_episodic_memory_candidates=0),
    )

    # The product facade owns its session while RuntimeFactory scopes are
    # disposable. Close the Core scope immediately after execution in this
    # SQLite fixture so terminal turn projection cannot contend with a stale
    # runtime transaction.
    class FixtureFactory:
        def build_run_scope(self, agent_run_id):
            scope = factory.build_run_scope(agent_run_id)
            execute = scope.run_runtime.execute
            def execute_and_release(**kwargs):
                try:
                    return execute(**kwargs)
                finally:
                    scope.close()
            scope.run_runtime.execute = execute_and_release
            return scope

    seen_context = []

    def resolve(message, prior_turns):
        seen_context.append([turn.user_message for turn in prior_turns])
        if not prior_turns:
            return message
        return "Summarize the transit demand forecasting paper identified in Turn 1"

    product = TransitScholarProduct(
        session, FixtureFactory(), goal_resolver=ConversationGoalResolver(resolve)
    )
    conversation = product.create_conversation(workspace.workspace_id, title="Research")

    first = product.submit_message(conversation.id, "Find the transit demand forecasting paper")
    second = product.submit_message(conversation.id, "Summarize that paper")

    assert first.status == second.status == "completed"
    assert first.agent_run_id and second.agent_run_id
    assert first.agent_run_id != second.agent_run_id
    assert first.resolved_user_goal == "Find the transit demand forecasting paper"
    assert "transit demand forecasting" in second.resolved_user_goal
    # The resolver is only delegated to when bounded prior context exists;
    # Turn 1 is already a standalone goal and therefore bypasses the
    # generator, while Turn 2 receives Turn 1's completed dialogue.
    assert seen_context == [[first.user_message]]
    assert first.final_assistant_response["answer_text"].startswith("Answer for:")
    assert second.final_assistant_response["answer_text"].startswith("Answer for:")

    runs = session.query(AgentRun).filter_by(workspace_id=workspace.workspace_id).all()
    assert {run.id for run in runs} == {first.agent_run_id, second.agent_run_id}
    assert all(run.status == "completed" for run in runs)
    assert session.query(EvidenceRecord).count() == 0

    view = product.read_conversation(conversation.id)
    assert [turn["sequence"] for turn in view["turns"]] == [1, 2]
    assert view["turns"][1]["run_state"].workspace_id == workspace.workspace_id
    assert view["turns"][1]["run_state"].phase == "completed"
