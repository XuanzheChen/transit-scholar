import json

import pytest

from transit_scholar.layer3.planning import RunDecision
from transit_scholar.layer3.roles.run_coordinator import build_run_coordinator
from transit_scholar.layer3.run_context import (
    RunContextSnapshotBuilder,
    RunCoordinatorContext,
    RunCoordinatorContextProjector,
    RunRuntimeConfig,
    SessionOutcome,
)


def _snapshot(*outcomes: SessionOutcome, goal: str = "research goal"):
    return RunContextSnapshotBuilder().build(
        agent_run={"agent_run_id": "run-1", "user_goal": goal},
        session_outcomes=outcomes,
        claim_refs=["run-claim"],
        unresolved_items=["unresolved-1", "unresolved-2"],
        conflicting_items=["conflict-1", "conflict-2"],
    )


def test_projector_excludes_raw_provenance_and_bounds_research_results():
    outcomes = [
        SessionOutcome(
            research_session_id=f"session-{index}",
            research_question=f"question-{index}",
            status="completed",
            final_summary=f"summary-{index}",
            key_claims=[f"claim-{index}-a", f"claim-{index}-b"],
            claim_refs=[f"claim-ref-{index}"],
            source_provenance=[{"evidence_id": "e-1", "text_snapshot": "SECRET-EVIDENCE-TEXT"}],
        )
        for index in range(4)
    ]
    config = RunRuntimeConfig(
        max_prior_sessions=2,
        max_claims_per_session=1,
        max_coordination_claims=1,
        max_coordination_claim_refs=1,
        max_coordination_unresolved_items=1,
        max_coordination_conflicting_items=1,
        max_handoff_items=10,
        max_serialized_chars=2000,
    )

    context = RunCoordinatorContextProjector(config).project(_snapshot(*outcomes))

    assert isinstance(context, RunCoordinatorContext)
    assert [item.research_session_id for item in context.prior_sessions] == [
        "session-2",
        "session-3",
    ]
    assert len(context.key_claims) == 1
    assert len(context.claim_refs) == 1
    assert len(context.unresolved_items) == 1
    assert len(context.conflicting_items) == 1
    assert "SECRET-EVIDENCE-TEXT" not in context.model_dump_json()
    assert "source_provenance" not in context.model_dump_json()
    assert len(context.model_dump_json()) <= config.max_serialized_chars


def test_production_semantic_decider_receives_projected_context_and_prompt_is_safe():
    class Client:
        def __init__(self):
            self.calls = []

        def generate_structured(self, messages, output_schema, metadata):
            self.calls.append((messages, output_schema, metadata))
            return RunDecision(mode="complete", completion_reason="sufficient")

    snapshot = _snapshot(
        SessionOutcome(
            research_session_id="session-1",
            research_question="question",
            status="completed",
            final_summary="bounded summary",
            source_provenance=[{"text_snapshot": "SECRET-EVIDENCE-TEXT"}],
        )
    )
    client = Client()
    coordinator = build_run_coordinator(llm_client=client)

    assert coordinator(snapshot).mode == "complete"
    assert len(client.calls) == 1
    prompt = client.calls[0][0][1]["content"]
    assert "RunCoordinatorContext" in prompt
    assert "bounded summary" in prompt
    assert "SECRET-EVIDENCE-TEXT" not in prompt
    assert "source_provenance" not in prompt


def test_object_semantic_decider_receives_projected_context_without_marker():
    class Decider:
        def __init__(self):
            self.context = None

        def decide(self, context):
            self.context = context
            return {"mode": "complete", "completion_reason": "sufficient"}

    decider = Decider()
    coordinator = build_run_coordinator(semantic_decider=decider)

    assert coordinator(_snapshot()).mode == "complete"
    assert isinstance(decider.context, RunCoordinatorContext)


def test_projector_fails_when_required_fields_cannot_fit():
    snapshot = _snapshot(goal="goal that cannot fit")
    with pytest.raises(ValueError, match="required coordination"):
        RunCoordinatorContextProjector(
            RunRuntimeConfig(max_serialized_chars=20)
        ).project(snapshot)


def test_recording_llm_receives_exact_bounded_payload_without_execution_history():
    class RecordingClient:
        def __init__(self):
            self.calls = []

        def generate_structured(self, messages, output_schema, metadata):
            self.calls.append(
                {
                    "messages": messages,
                    "output_schema": output_schema,
                    "metadata": metadata,
                }
            )
            return {"mode": "complete", "completion_reason": "sufficient"}

    outcomes = [
        SessionOutcome(
            research_session_id=f"session-{index}",
            research_question=f"question-{index}",
            status="completed" if index < 3 else "failed",
            final_summary=f"summary-{index}",
            failure_reason=f"failure-{index}" if index == 3 else None,
            key_claims=[
                {"claim_id": f"claim-{index}-a", "statement": f"claim-{index}-a"},
                {"claim_id": f"claim-{index}-b", "statement": f"claim-{index}-b"},
            ],
            claim_refs=[f"claim-ref-{index}-a", f"claim-ref-{index}-b"],
            evidence_refs=[f"evidence-ref-{index}"],
            source_refs=[f"source-ref-{index}"],
            source_provenance=[
                {
                    "evidence_id": f"evidence-{index}",
                    "text_snapshot": "SECRET-EVIDENCE-TEXT",
                    "agent_trace": [{"event": "retrieval"}],
                    "retrieval_history": [{"query": "private query"}],
                    "provider_history": [{"response": "private response"}],
                    "role_execution_history": [{"role": "private role"}],
                    "prompt_history": [{"prompt": "private prompt"}],
                }
            ],
        )
        for index in range(4)
    ]
    config = RunRuntimeConfig(
        max_prior_sessions=2,
        max_claims_per_session=1,
        max_coordination_claims=2,
        max_coordination_claim_refs=2,
        max_coordination_unresolved_items=1,
        max_coordination_conflicting_items=1,
        max_handoff_items=10,
        max_serialized_chars=3000,
    )
    snapshot = _snapshot(
        *outcomes,
        goal="bounded coordination goal",
    )
    client = RecordingClient()
    coordinator = build_run_coordinator(
        llm_client=client,
        context_projector=RunCoordinatorContextProjector(config),
    )

    assert coordinator(snapshot).mode == "complete"
    call = client.calls[0]
    assert call["output_schema"] is RunDecision
    assert call["metadata"] == {
        "prompt_key": "run_coordinator",
        "agent_run_id": "run-1",
    }
    user_prompt = call["messages"][1]["content"]
    payload = json.loads(user_prompt.split("RunCoordinatorContext:\n", 1)[1])

    assert payload["agent_run_id"] == "run-1"
    assert payload["user_goal"] == "bounded coordination goal"
    assert [item["research_session_id"] for item in payload["prior_sessions"]] == [
        "session-2",
        "session-3",
    ]
    assert payload["key_claims"] == ["claim-2-a", "claim-3-a"]
    assert payload["claim_refs"] == ["run-claim", "claim-ref-2-a"]
    assert payload["unresolved_items"] == ["unresolved-1"]
    assert payload["conflicting_items"] == ["conflict-1"]
    serialized_prompt = json.dumps(call["messages"], ensure_ascii=False, sort_keys=True)
    for forbidden in (
        "SECRET-EVIDENCE-TEXT",
        "source_provenance",
        "agent_trace",
        "retrieval_history",
        "provider_history",
        "role_execution_history",
        "prompt_history",
        "private query",
        "private response",
    ):
        assert forbidden not in serialized_prompt


def test_large_projection_is_repeatable_and_satisfies_all_configured_bounds():
    outcomes = [
        SessionOutcome(
            research_session_id=f"session-{index}",
            research_question=f"question-{index}",
            status="completed",
            final_summary="summary-" + ("x" * 3000),
            key_claims=[f"claim-{index}-{claim}" + ("y" * 1000) for claim in range(5)],
            claim_refs=[f"claim-ref-{index}-{ref}" for ref in range(5)],
            source_provenance=[{"text_snapshot": "SECRET-EVIDENCE-TEXT"}],
        )
        for index in range(8)
    ]
    config = RunRuntimeConfig(
        max_prior_sessions=3,
        max_claims_per_session=2,
        max_coordination_claims=4,
        max_coordination_claim_refs=3,
        max_coordination_unresolved_items=2,
        max_coordination_conflicting_items=2,
        max_handoff_items=10,
        max_serialized_chars=30000,
    )
    snapshot = _snapshot(*outcomes)
    projector = RunCoordinatorContextProjector(config)

    first = projector.project(snapshot)
    second = projector.project(snapshot)

    assert first.model_dump_json() == second.model_dump_json()
    assert len(first.prior_sessions) == 3
    assert len(first.key_claims) == 4
    assert len(first.claim_refs) == 3
    assert len(first.unresolved_items) == 2
    assert len(first.conflicting_items) == 2
    assert len(first.model_dump_json()) <= config.max_serialized_chars
    assert "SECRET-EVIDENCE-TEXT" not in first.model_dump_json()
