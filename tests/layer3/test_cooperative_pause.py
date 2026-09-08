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

    def update_agent_run_status(self, run_id, status):
        self.statuses.append((run_id, status))


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
