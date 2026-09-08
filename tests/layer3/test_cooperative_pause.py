from transit_scholar.layer3.planning import RunDecision
from transit_scholar.layer3.runtime import RunResearchRuntime


class MemoryStateStore:
    def __init__(self):
        self.payload = None

    def load(self, _run_id):
        return self.payload

    def save(self, _run_id, payload):
        self.payload = payload


class Execution:
    def __init__(self):
        self.statuses = []
        self.sessions = {}

    def update_agent_run_status(self, run_id, status):
        self.statuses.append((run_id, status))

    def create_research_session(self, *, agent_run_id, research_session_id, research_question):
        self.sessions[research_session_id] = {
            "research_session_id": research_session_id,
            "research_question": research_question,
        }
        return self.sessions[research_session_id]

    def get_research_session(self, _agent_run_id, research_session_id):
        return self.sessions[research_session_id]


def test_pause_checkpoint_persists_and_resume_continues_same_agent_run():
    store = MemoryStateStore()
    execution = Execution()
    requested = [True]
    runtime = RunResearchRuntime(
        session_runtime=object(),
        coordinator=lambda _snapshot: RunDecision(mode="complete", completion_reason="done"),
        execution_service=execution,
        state_store=store,
        is_pause_requested=lambda: requested[0],
    )

    paused = runtime.execute(agent_run_id="run-1", agent_run={"agent_run_id": "run-1", "user_goal": "Goal"})
    assert paused["status"] == "paused"
    assert store.payload["orchestration_state"]["agent_run_id"] == "run-1"
    assert execution.statuses == [("run-1", "paused")]

    requested[0] = False
    resumed = runtime.execute(agent_run_id="run-1", agent_run={"agent_run_id": "run-1", "user_goal": "Goal"})
    assert resumed["status"] == "completed"
    assert resumed["orchestration_state"].agent_run_id == "run-1"


class PausingSessionRuntime:
    def __init__(self, pause_request):
        self.calls = []
        self.pause_request = pause_request

    def execute(self, *, agent_run_id, research_session_id, session_handoff, is_pause_requested):
        self.calls.append(("execute", agent_run_id, research_session_id))
        self.pause_request[0] = True
        return {"status": "paused" if is_pause_requested() else "completed"}

    def resume_session(self, *, agent_run_id, research_session_id, session_handoff, is_pause_requested):
        self.calls.append(("resume", agent_run_id, research_session_id))
        return {"status": "paused" if is_pause_requested() else "completed"}


def test_pause_during_active_session_preserves_session_for_same_run_resume():
    store = MemoryStateStore()
    execution = Execution()
    requested = [False]
    session_runtime = PausingSessionRuntime(requested)
    runtime = RunResearchRuntime(
        session_runtime=session_runtime,
        coordinator=lambda snapshot: RunDecision(
            mode="complete" if snapshot.session_outcomes else "direct_session",
            proposed_questions=[] if snapshot.session_outcomes else ["Question"],
            completion_reason="done" if snapshot.session_outcomes else None,
        ),
        execution_service=execution,
        state_store=store,
        is_pause_requested=lambda: requested[0],
    )

    paused = runtime.execute(agent_run_id="run-1", agent_run={"agent_run_id": "run-1", "user_goal": "Goal"})
    active_session_id = paused["orchestration_state"].current_research_session_id
    assert paused["status"] == "paused"
    assert active_session_id is not None
    assert execution.statuses == [("run-1", "paused")]

    requested[0] = False
    resumed = runtime.execute(agent_run_id="run-1", agent_run={"agent_run_id": "run-1", "user_goal": "Goal"})
    assert resumed["orchestration_state"].agent_run_id == "run-1"
    assert session_runtime.calls[1] == ("resume", "run-1", active_session_id)
