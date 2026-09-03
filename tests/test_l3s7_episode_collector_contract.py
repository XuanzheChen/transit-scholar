from types import SimpleNamespace

from transit_scholar.layer3.memory import EpisodicMemoryCollector


def test_collector_preserves_authoritative_fields_and_session_ids() -> None:
    run = SimpleNamespace(
        workspace_id="workspace-1",
        agent_run_id="run-1",
        user_goal="Keep the user's exact wording.",
        research_sessions=[
            SimpleNamespace(research_session_id="session-1"),
            SimpleNamespace(research_session_id="session-2"),
        ],
        final_outcome="completed with caveats",
    )

    normalized = EpisodicMemoryCollector().collect(run)

    assert normalized.workspace_id == "workspace-1"
    assert normalized.agent_run_id == "run-1"
    assert normalized.user_goal_raw == "Keep the user's exact wording."
    assert normalized.session_ids == ("session-1", "session-2")
    assert normalized.final_outcome == "completed with caveats"
    assert normalized.durable_state_refs == ("agent-run:run-1",)


def test_collector_derives_useful_and_failed_query_experience() -> None:
    queries = [
        {"query_id": "q-useful", "query_text": "useful", "status": "completed"},
        {"query_id": "q-empty", "query_text": "no admitted evidence", "status": "completed"},
        {"query_id": "q-abandoned", "query_text": "abandoned", "status": "abandoned"},
        {"query_id": "q-failed", "query_text": "failed", "status": "failed"},
    ]
    evidence = [
        {"evidence_id": "e-1", "source_query_id": "q-useful"},
        {"evidence_id": "e-2", "source_query_id": "q-empty", "status": "rejected"},
    ]

    normalized = EpisodicMemoryCollector().collect(
        {"workspace_id": "workspace-1", "agent_run_id": "run-1", "user_goal": "goal"},
        queries=queries,
        evidence=evidence,
    )

    assert normalized.useful_queries == ("useful",)
    assert normalized.failed_or_unhelpful_queries == (
        "no admitted evidence",
        "abandoned",
        "failed",
    )


def test_terminal_failure_precedes_admitted_evidence() -> None:
    normalized = EpisodicMemoryCollector().collect(
        {"workspace_id": "workspace-1", "agent_run_id": "run-1", "user_goal": "goal"},
        queries=[
            {"query_id": "failed", "query_text": "failed after hit", "status": "failed"},
            {"query_id": "abandoned", "query_text": "abandoned after hit", "status": "abandoned"},
            {"query_id": "completed", "query_text": "completed hit", "status": "completed"},
        ],
        evidence=[
            {"evidence_id": "e-1", "source_query_id": "failed", "status": "admitted"},
            {"evidence_id": "e-2", "source_query_id": "abandoned", "status": "admitted"},
            {"evidence_id": "e-3", "source_query_id": "completed", "status": "admitted"},
        ],
    )

    assert normalized.useful_queries == ("completed hit",)
    assert normalized.failed_or_unhelpful_queries == (
        "failed after hit",
        "abandoned after hit",
    )
