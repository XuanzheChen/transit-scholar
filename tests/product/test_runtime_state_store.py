import json

import pytest

from transit_scholar.product.runtime import (
    FileRunResearchStateStore,
    _CommitBeforeCheckpointStore,
    _CommitBeforeRoleCheckpointStore,
)


def test_run_state_reloads_from_fresh_store_instance(tmp_path):
    payload = {
        "orchestration_state": {
            "agent_run_id": "run-1",
            "status": "running",
            "completed_session_ids": ["session-1"],
        },
        "session_outcomes": [{"research_session_id": "session-1", "status": "completed"}],
        "research_plan": {"items": []},
    }

    FileRunResearchStateStore(tmp_path).save("run-1", payload)

    assert FileRunResearchStateStore(tmp_path).load("run-1") == payload
    assert (tmp_path / "run-1" / "run_state.json").exists()


def test_run_state_replacement_is_atomic_and_removes_temporary_file(tmp_path, monkeypatch):
    store = FileRunResearchStateStore(tmp_path)
    store.save("run-1", {"version": 1})
    target = tmp_path / "run-1" / "run_state.json"
    original = target.read_text(encoding="utf-8")

    def fail_replace(source, destination):
        raise OSError("replace failed")

    monkeypatch.setattr("transit_scholar.product.runtime.os.replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        store.save("run-1", {"version": 2})

    assert target.read_text(encoding="utf-8") == original
    assert list(target.parent.glob(".run_state.json.*.tmp")) == []
    assert json.loads(original) == {"version": 1}


@pytest.mark.parametrize("agent_run_id", ["", "../run-1", "run/one"])
def test_run_state_rejects_unsafe_agent_run_id(tmp_path, agent_run_id):
    with pytest.raises(ValueError, match="file-safe"):
        FileRunResearchStateStore(tmp_path).save(agent_run_id, {})


def test_checkpoint_commits_sql_session_before_publishing_state():
    events = []

    class Session:
        def commit(self):
            events.append("commit")

    class Store:
        def save(self, agent_run_id, payload):
            events.append(("save", agent_run_id, payload))

    store = _CommitBeforeCheckpointStore(Session(), Store())
    store.save("run-1", {"status": "completed"})

    assert events == ["commit", ("save", "run-1", {"status": "completed"})]


def test_role_checkpoint_commits_sql_session_before_publishing_state():
    events = []

    class Session:
        def commit(self):
            events.append("commit")

    class Store:
        def save(self, execution):
            events.append(("save", execution))

    store = _CommitBeforeRoleCheckpointStore(Session(), Store())
    store.save("role-execution")

    assert events == ["commit", ("save", "role-execution")]
