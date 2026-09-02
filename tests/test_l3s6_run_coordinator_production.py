import pytest

from transit_scholar.layer3.planning import RunDecision, StructuredRunSemanticDecider
from transit_scholar.layer3.roles.run_coordinator import (
    OptionalPlanningPolicy,
    RunCoordinatorRole,
    SemanticRunCoordinationPolicy,
    build_fallback_run_coordinator,
    build_run_coordinator,
)
from transit_scholar.layer3.run_context import (
    RunContextSnapshotBuilder,
    RunCoordinatorContext,
    SessionOutcome,
)


def _snapshot(goal: str) -> dict[str, str]:
    return {"agent_run_id": goal, "user_goal": goal}


def test_production_composition_invokes_injected_semantic_decider():
    calls = []

    def decider(snapshot):
        calls.append(snapshot)
        return {"mode": "direct_session", "proposed_questions": [snapshot.user_goal]}

    coordinator = build_run_coordinator(semantic_decider=decider)
    decision = coordinator(_snapshot("focused question"))

    assert decision == RunDecision(mode="direct_session", proposed_questions=["focused question"])
    assert [snapshot.user_goal for snapshot in calls] == ["focused question"]
    assert isinstance(coordinator.policy, SemanticRunCoordinationPolicy)
    assert coordinator.policy.semantic_decider is decider


def test_same_production_composition_can_route_fresh_goals_differently():
    def decider(snapshot):
        mode = "planned_research" if snapshot.user_goal.startswith("broad") else "direct_session"
        return {"mode": mode, "proposed_questions": [snapshot.user_goal]}

    coordinator = build_run_coordinator(semantic_decider=decider)

    assert coordinator(_snapshot("focused question")).mode == "direct_session"
    assert coordinator(_snapshot("broad literature review")).mode == "planned_research"


def test_default_production_composition_has_concrete_semantic_decider():
    coordinator = build_run_coordinator()

    assert isinstance(coordinator.policy, SemanticRunCoordinationPolicy)
    assert isinstance(coordinator.policy.semantic_decider, StructuredRunSemanticDecider)


def test_structured_production_decider_invokes_injected_llm_client():
    class Client:
        def __init__(self):
            self.calls = []

        def generate_structured(self, messages, output_schema, metadata):
            self.calls.append((messages, output_schema, metadata))
            return {"mode": "direct_session"}

    client = Client()
    coordinator = build_run_coordinator(llm_client=client)

    assert coordinator(_snapshot("semantic goal")).mode == "direct_session"
    assert len(client.calls) == 1
    assert client.calls[0][1] is RunDecision
    assert "semantic goal" in client.calls[0][0][1]["content"]


def test_production_structured_decider_boundary_receives_projected_context():
    class Client:
        def generate_structured(self, messages, output_schema, metadata):
            return {"mode": "complete", "completion_reason": "sufficient"}

    class CapturingDecider(StructuredRunSemanticDecider):
        def __init__(self):
            super().__init__(Client())
            self.contexts = []

        def decide(self, context):
            self.contexts.append(context)
            return super().decide(context)

    snapshot = RunContextSnapshotBuilder().build(
        agent_run={"agent_run_id": "run-1", "user_goal": "semantic goal"},
        session_outcomes=[
            SessionOutcome(
                research_session_id="session-1",
                research_question="question",
                status="completed",
                final_summary="bounded summary",
                source_provenance=[{"text_snapshot": "SECRET-EVIDENCE-TEXT"}],
            )
        ],
    )
    decider = CapturingDecider()

    assert build_run_coordinator(semantic_decider=decider)(snapshot).mode == "complete"
    assert len(decider.contexts) == 1
    assert isinstance(decider.contexts[0], RunCoordinatorContext)
    assert "SECRET-EVIDENCE-TEXT" not in decider.contexts[0].model_dump_json()


def test_deterministic_fallback_is_explicitly_injectable():
    coordinator = build_fallback_run_coordinator()

    assert isinstance(coordinator.policy, OptionalPlanningPolicy)
    assert coordinator(_snapshot("short focused question")).mode == "direct_session"
    assert coordinator(_snapshot("compare several approaches in a review")).mode == "planned_research"


def test_bare_role_construction_requires_explicit_policy_or_semantic_decider():
    with pytest.raises(ValueError, match="requires an explicit policy or semantic_decider"):
        RunCoordinatorRole()
