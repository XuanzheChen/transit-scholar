from datetime import datetime, timezone

import pytest

from transit_scholar.layer3.execution import AgentRunRecord, ResearchSessionRecord
from transit_scholar.layer3.state import ResearchStateRecord
from transit_scholar.layer3.working_memory import WorkingMemory, WorkingMemoryBoundaryError


def _run(*, workspace_id: str = "workspace-1", run_id: str = "run-1") -> AgentRunRecord:
    now = datetime.now(timezone.utc)
    return AgentRunRecord(
        agent_run_id=run_id,
        workspace_id=workspace_id,
        user_goal="goal",
        status="running",
        workspace_revision=1,
        created_at=now,
        updated_at=now,
    )


def test_working_memory_reads_authoritative_objects_by_reference() -> None:
    now = datetime.now(timezone.utc)
    agent_run = _run()
    session = ResearchSessionRecord(
        research_session_id="session-1",
        agent_run_id="run-1",
        research_question="question",
        status="running",
        created_at=now,
        updated_at=now,
    )
    research_state = ResearchStateRecord(
        research_session_id="session-1", payload={"step": 1}, created_at=now, updated_at=now
    )
    sessions = [session]
    states = {"session-1": research_state}

    memory = WorkingMemory(
        workspace_id="workspace-1",
        agent_run_id="run-1",
        agent_run=agent_run,
        research_sessions=sessions,
        research_states=states,
    )

    assert memory.agent_run is agent_run
    assert memory.research_sessions is sessions
    assert memory.research_states is states
    assert memory.research_state_for("session-1") is research_state
    assert not hasattr(memory, "save")
    assert not hasattr(memory, "model_dump")


def test_working_memory_rejects_cross_workspace_and_cross_run_sources() -> None:
    with pytest.raises(WorkingMemoryBoundaryError, match="another Workspace"):
        WorkingMemory(workspace_id="workspace-1", agent_run_id="run-1", agent_run=_run(workspace_id="workspace-2"))

    now = datetime.now(timezone.utc)
    foreign_session = ResearchSessionRecord(
        research_session_id="session-2",
        agent_run_id="run-2",
        research_question="question",
        status="running",
        created_at=now,
        updated_at=now,
    )
    with pytest.raises(WorkingMemoryBoundaryError, match="another AgentRun"):
        WorkingMemory(
            workspace_id="workspace-1",
            agent_run_id="run-1",
            agent_run=_run(),
            research_sessions=[foreign_session],
        )
