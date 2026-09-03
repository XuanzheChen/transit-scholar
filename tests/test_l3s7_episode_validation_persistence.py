from datetime import datetime, timezone
import errno

import pytest

from transit_scholar.layer3.memory import (
    EpisodicMemoryProvenance,
    EpisodicMemoryRecord,
    EpisodicMemoryStore,
)


def episode(workspace="ws", run="run"):
    return EpisodicMemoryRecord(
        memory_id=f"m-{run}", workspace_id=workspace, agent_run_id=run,
        user_goal_raw="goal", goal_summary="summary", research_summary="research",
        unresolved_summary="", final_outcome="done",
        created_at=datetime.now(timezone.utc),
        provenance=EpisodicMemoryProvenance(workspace_id=workspace, agent_run_id=run),
    )


def test_put_failure_does_not_publish_phantom_and_retry_succeeds(tmp_path, monkeypatch):
    store = EpisodicMemoryStore.for_workspace("ws", base_dir=tmp_path)
    record = episode()
    original = store._persist
    calls = 0

    def fail_once(records=None):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("simulated persistence failure")
        return original(records)

    monkeypatch.setattr(store, "_persist", fail_once)
    with pytest.raises(OSError):
        store.put(record)
    assert store.get_for_run(workspace_id="ws", agent_run_id="run") is None
    store.put(record)
    assert EpisodicMemoryStore.for_workspace("ws", base_dir=tmp_path).get_for_run(
        workspace_id="ws", agent_run_id="run"
    ) == record


def test_delete_failure_keeps_memory_consistent_and_retry_succeeds(tmp_path, monkeypatch):
    store = EpisodicMemoryStore.for_workspace("ws", base_dir=tmp_path)
    record = episode()
    store.put(record)
    original = store._persist
    monkeypatch.setattr(store, "_persist", lambda records=None: (_ for _ in ()).throw(OSError("fail")))
    with pytest.raises(OSError):
        store.delete_workspace("ws")
    assert store.get_for_run(workspace_id="ws", agent_run_id="run") == record
    monkeypatch.setattr(store, "_persist", original)
    store.delete_workspace("ws")
    assert store.get_for_run(workspace_id="ws", agent_run_id="run") is None


def test_unsupported_directory_fsync_after_replace_is_tolerated(tmp_path, monkeypatch):
    store = EpisodicMemoryStore.for_workspace("ws", base_dir=tmp_path)
    real_fsync = __import__("os").fsync
    calls = 0

    def fsync(fd):
        nonlocal calls
        calls += 1
        if calls > 1:
            raise OSError(errno.EINVAL, "directory fsync unsupported")
        return real_fsync(fd)

    monkeypatch.setattr("transit_scholar.layer3.memory.retrieval.os.fsync", fsync)
    store.put(episode())
    assert store.get_for_run(workspace_id="ws", agent_run_id="run") is not None


def test_directory_fsync_io_failure_propagates_without_state_loss(tmp_path, monkeypatch):
    store = EpisodicMemoryStore.for_workspace("ws", base_dir=tmp_path)
    monkeypatch.setattr("transit_scholar.layer3.memory.retrieval.os.fsync", lambda fd: (_ for _ in ()).throw(OSError(errno.EIO, "io failure")))
    with pytest.raises(OSError):
        store.put(episode())
    assert store.get_for_run(workspace_id="ws", agent_run_id="run") is None


def test_directory_fsync_permission_failure_propagates_without_state_loss(tmp_path, monkeypatch):
    store = EpisodicMemoryStore.for_workspace("ws", base_dir=tmp_path)
    os_module = __import__("os")
    real_fsync = os_module.fsync
    real_open = os_module.open
    calls = 0

    def open_directory(path, flags, *args, **kwargs):
        if path == store._storage_path.parent:
            return real_open(store._storage_path, os_module.O_RDONLY)
        return real_open(path, flags, *args, **kwargs)

    def fsync(fd):
        nonlocal calls
        calls += 1
        if calls > 1:
            raise OSError(errno.EACCES, "directory fsync denied")
        return real_fsync(fd)

    monkeypatch.setattr("transit_scholar.layer3.memory.retrieval.os.open", open_directory)
    monkeypatch.setattr("transit_scholar.layer3.memory.retrieval.os.fsync", fsync)
    with pytest.raises(OSError) as exc_info:
        store.put(episode())
    assert exc_info.value.errno == errno.EACCES
    assert store.get_for_run(workspace_id="ws", agent_run_id="run") is None


def test_replace_failure_propagates_without_publishing_state(tmp_path, monkeypatch):
    store = EpisodicMemoryStore.for_workspace("ws", base_dir=tmp_path)
    monkeypatch.setattr(
        "transit_scholar.layer3.memory.retrieval.os.replace",
        lambda source, target: (_ for _ in ()).throw(OSError(errno.EIO, "replace failed")),
    )
    with pytest.raises(OSError) as exc_info:
        store.put(episode())
    assert exc_info.value.errno == errno.EIO
    assert store.get_for_run(workspace_id="ws", agent_run_id="run") is None
