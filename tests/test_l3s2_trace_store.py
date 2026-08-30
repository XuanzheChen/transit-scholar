"""Layer3 Stage2 append-only AgentTrace persistence tests."""

from __future__ import annotations

import uuid

import pytest

from transit_scholar.db.engine import SessionLocal
from transit_scholar.layer3.execution import AgentRunService, ResearchSessionOwnershipError
from transit_scholar.layer3.trace import AgentTraceService
from transit_scholar.layer3.workspace import WorkspaceService


def _run_with_session(session):
    workspace_id = WorkspaceService(session).create(
        name="Trace workspace", workspace_id=uuid.uuid4().hex
    ).workspace.workspace_id
    execution = AgentRunService(session)
    run = execution.create_agent_run(workspace_id=workspace_id, user_goal="Trace goal")
    research_session = execution.create_research_session(
        agent_run_id=run.agent_run_id, research_question="Trace question"
    )
    return run, research_session


def test_trace_appends_all_events_in_deterministic_run_order(session):
    run, research_session = _run_with_session(session)
    traces = AgentTraceService(session)

    first = traces.append_event(
        agent_run_id=run.agent_run_id,
        event_type="context.assembled",
        payload={"sources": ["workspace"]},
    )
    second = traces.append_event(
        agent_run_id=run.agent_run_id,
        research_session_id=research_session.research_session_id,
        event_type="tool.future_result",
        payload={"tool": "search", "result_count": 3},
    )
    third = traces.append_event(
        agent_run_id=run.agent_run_id,
        event_type="runtime.extension.event",
        payload={"nested": {"version": 2}},
    )

    trace = traces.read_trace(agent_run_id=run.agent_run_id)
    assert [event.event_id for event in trace] == [first.event_id, second.event_id, third.event_id]
    assert [event.sequence for event in trace] == [1, 2, 3]
    assert trace[0].payload == {"sources": ["workspace"]}
    assert trace[1].payload == {"tool": "search", "result_count": 3}
    assert trace[2].event_type == "runtime.extension.event"


def test_trace_filters_session_events_without_losing_run_events(session):
    run, research_session = _run_with_session(session)
    traces = AgentTraceService(session)
    run_event = traces.append_event(
        agent_run_id=run.agent_run_id,
        event_type="run.started",
        payload={"source": "user"},
    )
    session_event = traces.append_event(
        agent_run_id=run.agent_run_id,
        research_session_id=research_session.research_session_id,
        event_type="research.started",
        payload={"question": "Trace question"},
    )

    full_trace = traces.read_trace(agent_run_id=run.agent_run_id)
    session_trace = traces.read_trace(
        agent_run_id=run.agent_run_id,
        research_session_id=research_session.research_session_id,
    )
    assert [event.event_id for event in full_trace] == [run_event.event_id, session_event.event_id]
    assert [event.event_id for event in session_trace] == [session_event.event_id]
    assert session_trace[0].research_session_id == research_session.research_session_id


def test_trace_reloads_across_fresh_database_session():
    first_session = SessionLocal()
    try:
        run, research_session = _run_with_session(first_session)
        traces = AgentTraceService(first_session)
        first = traces.append_event(
            agent_run_id=run.agent_run_id,
            event_type="llm.invocation",
            payload={"model": "future-model", "messages": 2},
        )
        second = traces.append_event(
            agent_run_id=run.agent_run_id,
            research_session_id=research_session.research_session_id,
            event_type="evidence.created",
            payload={"locator": {"paper_id": "paper-1", "page": 7}},
        )
        first_session.commit()
    finally:
        first_session.close()

    second_session = SessionLocal()
    try:
        reloaded = AgentTraceService(second_session).read_trace(
            agent_run_id=run.agent_run_id
        )
        assert [(event.event_id, event.sequence) for event in reloaded] == [
            (first.event_id, 1),
            (second.event_id, 2),
        ]
        assert reloaded[1].payload == {"locator": {"paper_id": "paper-1", "page": 7}}
    finally:
        second_session.close()


def test_trace_rejects_session_belonging_to_a_different_run(session):
    workspace_id = WorkspaceService(session).create(
        name="Trace ownership workspace", workspace_id=uuid.uuid4().hex
    ).workspace.workspace_id
    execution = AgentRunService(session)
    owner = execution.create_agent_run(workspace_id=workspace_id, user_goal="Owner")
    other = execution.create_agent_run(workspace_id=workspace_id, user_goal="Other")
    research_session = execution.create_research_session(
        agent_run_id=owner.agent_run_id, research_question="Owned question"
    )

    with pytest.raises(ResearchSessionOwnershipError):
        AgentTraceService(session).append_event(
            agent_run_id=other.agent_run_id,
            research_session_id=research_session.research_session_id,
            event_type="invalid.scope",
            payload={},
        )
