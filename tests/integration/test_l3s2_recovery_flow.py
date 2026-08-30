"""End-to-end recovery verification for Layer3 Stage2 execution records."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

from transit_scholar.db.engine import SessionLocal
from transit_scholar.layer3.execution import AgentRunService
from transit_scholar.layer3.state import ResearchStateService
from transit_scholar.layer3.trace import AgentTraceService
from transit_scholar.layer3.workspace import WorkspaceService


def test_stage2_lifecycle_recovers_across_a_fresh_process():
    """Persist a complete run, then reconstruct it in an independent process."""
    session = SessionLocal()
    try:
        workspace = WorkspaceService(session).create(
            name="Stage2 recovery workspace", workspace_id=uuid.uuid4().hex
        ).workspace
        execution = AgentRunService(session)
        run = execution.create_agent_run(
            workspace_id=workspace.workspace_id,
            user_goal="Compare transit signal priority evidence",
        )
        first = execution.create_research_session(
            agent_run_id=run.agent_run_id,
            research_question="Which evaluation metrics were reported?",
        )
        second = execution.create_research_session(
            agent_run_id=run.agent_run_id,
            research_question="Which scenarios support the reported outcomes?",
        )
        states = ResearchStateService(session)
        states.save_research_state(
            agent_run_id=run.agent_run_id,
            research_session_id=first.research_session_id,
            payload={"current_focus": "evaluation metrics", "open_questions": ["peak"]},
        )
        states.save_research_state(
            agent_run_id=run.agent_run_id,
            research_session_id=second.research_session_id,
            payload={"current_focus": "scenario evidence", "recent_operations": ["read"]},
        )
        traces = AgentTraceService(session)
        traces.append_event(
            agent_run_id=run.agent_run_id,
            event_type="run.started",
            payload={"origin": "user"},
        )
        traces.append_event(
            agent_run_id=run.agent_run_id,
            research_session_id=first.research_session_id,
            event_type="evidence.located",
            payload={"paper_id": "paper-1", "page": 8},
        )
        traces.append_event(
            agent_run_id=run.agent_run_id,
            research_session_id=second.research_session_id,
            event_type="future.runtime.event",
            payload={"extension": {"version": 1}},
        )
        session.commit()
    finally:
        session.close()

    repository_root = Path(__file__).resolve().parents[2]
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(repository_root / "src") + os.pathsep + environment.get(
        "PYTHONPATH", ""
    )
    recovery_program = """
import json
import sys
from transit_scholar.db.engine import SessionLocal
from transit_scholar.layer3.execution import AgentRunService
from transit_scholar.layer3.state import ResearchStateService
from transit_scholar.layer3.trace import AgentTraceService

run_id, first_id, second_id = sys.argv[1:]
session = SessionLocal()
try:
    execution = AgentRunService(session)
    states = ResearchStateService(session)
    trace = AgentTraceService(session)
    print(json.dumps({
        "run": execution.get_agent_run(run_id).model_dump(mode="json"),
        "sessions": [item.model_dump(mode="json") for item in execution.list_research_sessions(run_id)],
        "first_state": states.load_research_state(agent_run_id=run_id, research_session_id=first_id).model_dump(mode="json"),
        "second_state": states.load_research_state(agent_run_id=run_id, research_session_id=second_id).model_dump(mode="json"),
        "trace": [item.model_dump(mode="json") for item in trace.read_trace(agent_run_id=run_id)],
    }))
finally:
    session.close()
"""
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            recovery_program,
            run.agent_run_id,
            first.research_session_id,
            second.research_session_id,
        ],
        check=True,
        capture_output=True,
        text=True,
        cwd=repository_root,
        env=environment,
    )
    recovered = json.loads(completed.stdout)

    assert recovered["run"]["workspace_id"] == workspace.workspace_id
    assert recovered["run"]["workspace_revision"] == workspace.revision
    assert recovered["run"]["user_goal"] == run.user_goal
    assert [item["research_session_id"] for item in recovered["sessions"]] == sorted(
        [first.research_session_id, second.research_session_id]
    )
    assert recovered["first_state"]["payload"] == {
        "current_focus": "evaluation metrics",
        "open_questions": ["peak"],
    }
    assert recovered["second_state"]["payload"] == {
        "current_focus": "scenario evidence",
        "recent_operations": ["read"],
    }
    assert [item["sequence"] for item in recovered["trace"]] == [1, 2, 3]
    assert [item["research_session_id"] for item in recovered["trace"]] == [
        None,
        first.research_session_id,
        second.research_session_id,
    ]
